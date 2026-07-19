"""Lease-fenced transactional Outbox dispatcher for durable Redis Streams."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from openrag.modules.events.envelopes import parse_registered_envelope
from openrag.modules.events.models import OutboxEvent
from openrag.modules.events.streams import stream_for_event_type

MAX_DISPATCH_BATCH = 100


class EventRedis(Protocol):
    async def xadd(
        self,
        name: str,
        fields: dict[bytes, bytes],
    ) -> bytes | str: ...

    async def waitaof(
        self,
        num_local: int,
        num_replicas: int,
        timeout: int,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class OutboxClaim:
    row_id: UUID
    event_id: UUID
    event_type: str
    payload: dict[str, object]
    envelope_digest: str
    lease_token: UUID
    attempts: int


DispatchResult = Literal["published", "retry", "dead_lettered", "lease_lost"]


def _db_utc_now() -> ColumnElement[datetime]:
    return cast(ColumnElement[datetime], func.timezone("UTC", func.now()))


async def claim_outbox(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    owner: str,
    batch_size: int = MAX_DISPATCH_BATCH,
    lease_seconds: int = 30,
) -> list[OutboxClaim]:
    """Claim eligible rows using database time and commit before I/O."""

    resolved_batch_size = max(1, min(batch_size, MAX_DISPATCH_BATCH))
    async with session_factory.begin() as session:
        now = await session.scalar(select(_db_utc_now()))
        if now is None:
            raise RuntimeError("database time unavailable")
        rows = list(
            (
                await session.scalars(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.published_at.is_(None),
                        OutboxEvent.dead_lettered_at.is_(None),
                        OutboxEvent.dispatch_after <= now,
                        or_(
                            OutboxEvent.lease_expires_at.is_(None),
                            OutboxEvent.lease_expires_at <= now,
                        ),
                    )
                    .order_by(
                        OutboxEvent.dispatch_after,
                        OutboxEvent.created_at,
                        OutboxEvent.event_id,
                    )
                    .limit(resolved_batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        claims: list[OutboxClaim] = []
        for row in rows:
            lease_token = uuid4()
            row.lease_owner = owner
            row.lease_token = lease_token
            row.lease_expires_at = now + timedelta(seconds=lease_seconds)
            row.attempts += 1
            if row.envelope_digest is None:
                row.dead_lettered_at = now
                row.last_error_code = "contract_invalid"
                row.lease_owner = None
                row.lease_token = None
                row.lease_expires_at = None
                continue
            claims.append(
                OutboxClaim(
                    row_id=row.id,
                    event_id=row.event_id,
                    event_type=row.event_type,
                    payload=dict(row.payload),
                    envelope_digest=row.envelope_digest,
                    lease_token=lease_token,
                    attempts=row.attempts,
                )
            )
    return claims


def _canonical_claim_bytes(claim: OutboxClaim) -> bytes:
    encoded = json.dumps(
        claim.payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    envelope = parse_registered_envelope(encoded)
    if envelope.event_id != claim.event_id or envelope.event_type != claim.event_type:
        raise ValueError("contract_invalid")
    if hashlib.sha256(encoded).hexdigest() != claim.envelope_digest:
        raise ValueError("contract_invalid")
    return encoded


def _aof_confirmed(result: object) -> bool:
    if isinstance(result, (tuple, list)) and result:
        return isinstance(result[0], int) and result[0] >= 1
    return isinstance(result, int) and result >= 1


async def _mark_published(
    session_factory: async_sessionmaker[AsyncSession],
    claim: OutboxClaim,
    *,
    stream: str,
    message_id: str,
) -> bool:
    async with session_factory.begin() as session:
        result = await session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.id == claim.row_id,
                OutboxEvent.lease_token == claim.lease_token,
                OutboxEvent.published_at.is_(None),
                OutboxEvent.dead_lettered_at.is_(None),
            )
            .values(
                published_at=_db_utc_now(),
                published_stream=stream,
                published_message_id=message_id,
                last_error_code=None,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
            )
            .returning(OutboxEvent.id)
        )
    return result.scalar_one_or_none() is not None


async def _release_for_retry(
    session_factory: async_sessionmaker[AsyncSession],
    claim: OutboxClaim,
    *,
    error_code: str,
) -> bool:
    delay_seconds = min(2 ** min(claim.attempts, 8), 300)
    async with session_factory.begin() as session:
        result = await session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.id == claim.row_id,
                OutboxEvent.lease_token == claim.lease_token,
                OutboxEvent.published_at.is_(None),
                OutboxEvent.dead_lettered_at.is_(None),
            )
            .values(
                dispatch_after=_db_utc_now() + timedelta(seconds=delay_seconds),
                last_error_code=error_code,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
            )
            .returning(OutboxEvent.id)
        )
    return result.scalar_one_or_none() is not None


async def _dead_letter(
    session_factory: async_sessionmaker[AsyncSession],
    claim: OutboxClaim,
    *,
    error_code: str,
) -> bool:
    async with session_factory.begin() as session:
        result = await session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.id == claim.row_id,
                OutboxEvent.lease_token == claim.lease_token,
                OutboxEvent.published_at.is_(None),
                OutboxEvent.dead_lettered_at.is_(None),
            )
            .values(
                dead_lettered_at=_db_utc_now(),
                last_error_code=error_code,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
            )
            .returning(OutboxEvent.id)
        )
    return result.scalar_one_or_none() is not None


async def dispatch_claim(
    session_factory: async_sessionmaker[AsyncSession],
    redis: EventRedis,
    claim: OutboxClaim,
    *,
    waitaof_timeout_ms: int,
) -> DispatchResult:
    """Publish one claim, confirm AOF durability, then fence its DB outcome."""

    try:
        encoded = _canonical_claim_bytes(claim)
        stream = stream_for_event_type(claim.event_type)
    except ValueError as exc:
        error_code = str(exc)
        if error_code == "schema_not_registered":
            released = await _release_for_retry(
                session_factory,
                claim,
                error_code="schema_not_registered",
            )
            return "retry" if released else "lease_lost"
        dead_lettered = await _dead_letter(
            session_factory,
            claim,
            error_code="contract_invalid",
        )
        return "dead_lettered" if dead_lettered else "lease_lost"

    try:
        message_id = await redis.xadd(
            stream,
            {
                b"envelope_bytes": encoded,
                b"envelope_digest": claim.envelope_digest.encode("ascii"),
            },
        )
        durability = await redis.waitaof(1, 0, waitaof_timeout_ms)
    except Exception:
        released = await _release_for_retry(
            session_factory,
            claim,
            error_code="event_transport_unavailable",
        )
        return "retry" if released else "lease_lost"

    if not _aof_confirmed(durability):
        released = await _release_for_retry(
            session_factory,
            claim,
            error_code="event_durability_unconfirmed",
        )
        return "retry" if released else "lease_lost"

    published = await _mark_published(
        session_factory,
        claim,
        stream=stream,
        message_id=(
            message_id.decode("ascii")
            if isinstance(message_id, bytes)
            else message_id
        ),
    )
    return "published" if published else "lease_lost"
