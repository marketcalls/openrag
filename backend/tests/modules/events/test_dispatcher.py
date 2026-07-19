import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.db import naive_utc
from openrag.modules.documents.lifecycle import DocumentVersionState
from openrag.modules.events.dispatcher import (
    claim_outbox,
    dispatch_claim,
)
from openrag.modules.events.envelopes import (
    DocumentVersionLifecycleV1,
    canonical_envelope_bytes,
    parse_registered_envelope,
)
from openrag.modules.events.models import OutboxEvent
from openrag.modules.events.outbox import add_registered_event
from openrag.modules.events.streams import DOCUMENT_EVENTS_STREAM


class RecordingRedis:
    def __init__(self, *, durability: tuple[int, int] = (1, 0)) -> None:
        self.durability = durability
        self.calls: list[tuple[str, object]] = []

    async def xadd(
        self,
        name: str,
        fields: dict[bytes, bytes],
    ) -> bytes:
        self.calls.append(("xadd", (name, fields)))
        return b"1700000000000-0"

    async def waitaof(
        self,
        num_local: int,
        num_replicas: int,
        timeout: int,
    ) -> tuple[int, int]:
        self.calls.append(
            ("waitaof", (num_local, num_replicas, timeout))
        )
        return self.durability


async def _seed_event(session: AsyncSession) -> OutboxEvent:
    event = add_registered_event(
        session,
        payload=DocumentVersionLifecycleV1(
            document_id=UUID("10000000-0000-0000-0000-000000000001"),
            previous_state=DocumentVersionState.REVIEW,
            new_state=DocumentVersionState.APPROVED,
        ),
        org_id=UUID("20000000-0000-0000-0000-000000000002"),
        workspace_id=UUID("30000000-0000-0000-0000-000000000003"),
        aggregate_id=UUID("40000000-0000-0000-0000-000000000004"),
        lifecycle_revision=2,
        correlation_id=UUID("50000000-0000-0000-0000-000000000005"),
        occurred_at=datetime(2026, 7, 19, 12, tzinfo=UTC),
        event_id=UUID("60000000-0000-0000-0000-000000000006"),
    )
    await session.commit()
    return event


@pytest.fixture
async def event_factory(engine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:  # type: ignore[no-untyped-def]
    yield async_sessionmaker(engine, expire_on_commit=False)


async def test_claim_uses_a_unique_lease_and_commits_before_returning(
    event_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with event_factory() as session:
        event = await _seed_event(session)

    claims = await claim_outbox(
        event_factory,
        owner="events-worker-a",
        batch_size=100,
        lease_seconds=30,
    )

    assert len(claims) == 1
    claim = claims[0]
    assert claim.row_id == event.id
    assert claim.lease_token != UUID(int=0)
    assert claim.attempts == 1

    async with event_factory() as verification:
        persisted = await verification.get(OutboxEvent, event.id)
        assert persisted is not None
        assert persisted.lease_owner == "events-worker-a"
        assert persisted.lease_token == claim.lease_token
        assert persisted.attempts == 1


async def test_dispatch_writes_exact_attested_bytes_then_confirms_aof(
    event_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with event_factory() as session:
        event = await _seed_event(session)
    claim = (
        await claim_outbox(
            event_factory,
            owner="events-worker-a",
            batch_size=1,
            lease_seconds=30,
        )
    )[0]
    redis = RecordingRedis()

    result = await dispatch_claim(
        event_factory,
        redis,
        claim,
        waitaof_timeout_ms=5000,
    )

    assert result == "published"
    assert [name for name, _ in redis.calls] == ["xadd", "waitaof"]
    stream, fields = redis.calls[0][1]  # type: ignore[misc]
    assert stream == DOCUMENT_EVENTS_STREAM
    assert set(fields) == {b"envelope_bytes", b"envelope_digest"}
    encoded = fields[b"envelope_bytes"]
    envelope = parse_registered_envelope(encoded)
    assert canonical_envelope_bytes(envelope) == encoded
    assert fields[b"envelope_digest"].decode() == hashlib.sha256(
        encoded
    ).hexdigest()

    async with event_factory() as verification:
        persisted = await verification.get(OutboxEvent, event.id)
        assert persisted is not None
        assert persisted.published_at is not None
        assert persisted.published_stream == DOCUMENT_EVENTS_STREAM
        assert persisted.published_message_id == "1700000000000-0"
        assert persisted.lease_token is None


async def test_unconfirmed_aof_releases_the_lease_for_retry(
    event_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with event_factory() as session:
        event = await _seed_event(session)
    claim = (
        await claim_outbox(
            event_factory,
            owner="events-worker-a",
            batch_size=1,
            lease_seconds=30,
        )
    )[0]

    result = await dispatch_claim(
        event_factory,
        RecordingRedis(durability=(0, 0)),
        claim,
        waitaof_timeout_ms=10,
    )

    assert result == "retry"
    async with event_factory() as verification:
        persisted = await verification.get(OutboxEvent, event.id)
        assert persisted is not None
        assert persisted.published_at is None
        assert persisted.last_error_code == "event_durability_unconfirmed"
        assert persisted.lease_owner is None
        assert persisted.lease_token is None
        assert persisted.dispatch_after > naive_utc()


async def test_stale_lease_token_cannot_publish_or_release_newer_claim(
    event_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with event_factory() as session:
        event = await _seed_event(session)
    claim = (
        await claim_outbox(
            event_factory,
            owner="events-worker-a",
            batch_size=1,
            lease_seconds=30,
        )
    )[0]
    replacement = uuid4()
    async with event_factory.begin() as session:
        persisted = await session.get(OutboxEvent, event.id)
        assert persisted is not None
        persisted.lease_token = replacement

    result = await dispatch_claim(
        event_factory,
        RecordingRedis(),
        claim,
        waitaof_timeout_ms=10,
    )

    assert result == "lease_lost"
    async with event_factory() as verification:
        persisted = await verification.scalar(
            select(OutboxEvent).where(OutboxEvent.id == event.id)
        )
        assert persisted is not None
        assert persisted.published_at is None
        assert persisted.lease_token == replacement
        assert persisted.last_error_code is None
