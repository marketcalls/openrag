import asyncio
import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from openrag.modules.documents.lifecycle import DocumentVersionState
from openrag.modules.events.consumer import (
    EventAcknowledgementError,
    StreamDelivery,
    consume_one,
    revalidate_document_lifecycle,
)
from openrag.modules.events.envelopes import (
    DocumentVersionLifecycleV1,
    build_envelope,
)
from openrag.modules.events.models import InboxEvent, OutboxEvent
from openrag.modules.events.outbox import add_registered_event
from openrag.modules.events.streams import (
    DOCUMENT_EVENTS_DLQ_STREAM,
    DOCUMENT_EVENTS_GROUP,
    DOCUMENT_EVENTS_STREAM,
)

CONSUMER = "document-projector-v1"


class RecordingRedis:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        fail_ack: bool = False,
    ) -> None:
        self.session_factory = session_factory
        self.fail_ack = fail_ack
        self.calls: list[tuple[str, object]] = []
        self.inbox_visible_at_ack = False

    async def xadd(
        self,
        name: str,
        fields: dict[bytes, bytes],
    ) -> bytes:
        self.calls.append(("xadd", (name, fields)))
        return b"1700000000001-0"

    async def waitaof(
        self,
        num_local: int,
        num_replicas: int,
        timeout: int,
    ) -> tuple[int, int]:
        self.calls.append(
            ("waitaof", (num_local, num_replicas, timeout))
        )
        return 1, 0

    async def xack(
        self,
        name: str,
        groupname: str,
        *ids: str,
    ) -> int:
        if self.session_factory is not None:
            async with self.session_factory() as session:
                count = await session.scalar(
                    select(func.count()).select_from(InboxEvent)
                )
                self.inbox_visible_at_ack = count == 1
        self.calls.append(("xack", (name, groupname, ids)))
        if self.fail_ack:
            raise ConnectionError("sentinel raw redis failure")
        return 1


async def _seed_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> OutboxEvent:
    async with session_factory.begin() as session:
        return add_registered_event(
            session,
            payload=DocumentVersionLifecycleV1(
                document_id=UUID("81000000-0000-0000-0000-000000000001"),
                previous_state=DocumentVersionState.REVIEW,
                new_state=DocumentVersionState.APPROVED,
            ),
            org_id=UUID("82000000-0000-0000-0000-000000000002"),
            workspace_id=UUID("83000000-0000-0000-0000-000000000003"),
            aggregate_id=UUID("84000000-0000-0000-0000-000000000004"),
            lifecycle_revision=2,
            correlation_id=UUID("85000000-0000-0000-0000-000000000005"),
            occurred_at=datetime(2026, 7, 20, 2, tzinfo=UTC),
            event_id=UUID("86000000-0000-0000-0000-000000000006"),
        )


def _delivery(
    event: OutboxEvent,
    *,
    delivery_count: int = 1,
    digest: bytes | None = None,
    extra_field: bool = False,
) -> StreamDelivery:
    encoded = json.dumps(
        event.payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    fields = {
        b"envelope_bytes": encoded,
        b"envelope_digest": digest or event.envelope_digest.encode(),
    }
    if extra_field:
        fields[b"raw_error"] = b"SENTINEL"
    return StreamDelivery(
        stream=DOCUMENT_EVENTS_STREAM,
        group=DOCUMENT_EVENTS_GROUP,
        message_id="1700000000000-0",
        fields=fields,
        delivery_count=delivery_count,
    )


@pytest.fixture
def event_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def test_first_delivery_commits_inbox_and_effect_before_ack(
    event_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = await _seed_event(event_factory)
    redis = RecordingRedis(session_factory=event_factory)
    calls: list[str] = []

    async def revalidate(_session: AsyncSession, _envelope: object) -> None:
        calls.append("revalidate")

    async def effect(_session: AsyncSession, _envelope: object) -> None:
        calls.append("effect")

    result = await consume_one(
        event_factory,
        redis,
        consumer=CONSUMER,
        delivery=_delivery(event),
        revalidate=revalidate,
        apply_effect=effect,
    )

    assert result == "processed"
    assert calls == ["revalidate", "effect"]
    assert redis.inbox_visible_at_ack is True
    assert [name for name, _ in redis.calls] == ["xack"]


async def test_duplicate_delivery_applies_effect_once_and_acks_again(
    event_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = await _seed_event(event_factory)
    redis = RecordingRedis()
    effects: list[UUID] = []

    async def revalidate(_session: AsyncSession, _envelope: object) -> None:
        return None

    async def effect(_session: AsyncSession, envelope: object) -> None:
        effects.append(envelope.event_id)  # type: ignore[attr-defined]

    first = await consume_one(
        event_factory,
        redis,
        consumer=CONSUMER,
        delivery=_delivery(event),
        revalidate=revalidate,
        apply_effect=effect,
    )
    duplicate = await consume_one(
        event_factory,
        redis,
        consumer=CONSUMER,
        delivery=_delivery(event),
        revalidate=revalidate,
        apply_effect=effect,
    )

    assert (first, duplicate) == ("processed", "duplicate")
    assert effects == [event.event_id]
    assert [name for name, _ in redis.calls] == ["xack", "xack"]


async def test_concurrent_duplicate_delivery_commits_one_logical_effect(
    event_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = await _seed_event(event_factory)
    redis = RecordingRedis()
    effects: list[bool] = []

    async def revalidate(_session: AsyncSession, _envelope: object) -> None:
        return None

    async def effect(_session: AsyncSession, _envelope: object) -> None:
        effects.append(True)

    results = await asyncio.gather(
        *(
            consume_one(
                event_factory,
                redis,
                consumer=CONSUMER,
                delivery=_delivery(event),
                revalidate=revalidate,
                apply_effect=effect,
            )
            for _ in range(2)
        )
    )

    assert sorted(results) == ["duplicate", "processed"]
    assert effects == [True]
    assert [name for name, _ in redis.calls] == ["xack", "xack"]


async def test_effect_failure_rolls_back_and_does_not_ack(
    event_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = await _seed_event(event_factory)
    redis = RecordingRedis()

    async def revalidate(_session: AsyncSession, _envelope: object) -> None:
        return None

    async def fail(_session: AsyncSession, _envelope: object) -> None:
        raise RuntimeError("sentinel provider failure")

    with pytest.raises(RuntimeError, match="sentinel provider failure"):
        await consume_one(
            event_factory,
            redis,
            consumer=CONSUMER,
            delivery=_delivery(event),
            revalidate=revalidate,
            apply_effect=fail,
        )

    async with event_factory() as session:
        assert await session.scalar(
            select(func.count()).select_from(InboxEvent)
        ) == 0
    assert redis.calls == []


async def test_ack_failure_preserves_committed_effect_for_redelivery(
    event_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = await _seed_event(event_factory)
    redis = RecordingRedis(fail_ack=True)
    effects: list[bool] = []

    async def revalidate(_session: AsyncSession, _envelope: object) -> None:
        return None

    async def effect(_session: AsyncSession, _envelope: object) -> None:
        effects.append(True)

    with pytest.raises(EventAcknowledgementError):
        await consume_one(
            event_factory,
            redis,
            consumer=CONSUMER,
            delivery=_delivery(event),
            revalidate=revalidate,
            apply_effect=effect,
        )

    async with event_factory() as session:
        assert await session.scalar(
            select(func.count()).select_from(InboxEvent)
        ) == 1
    assert effects == [True]


async def test_attested_future_schema_is_deferred_without_ack_or_inbox(
    event_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = await _seed_event(event_factory)
    event.payload["schema_version"] = 2
    event.payload["event_type"] = "document.version.future.v2"
    encoded = json.dumps(
        event.payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    event.event_type = "document.version.future.v2"
    event.envelope_digest = hashlib.sha256(encoded).hexdigest()
    async with event_factory.begin() as session:
        await session.merge(event)
    redis = RecordingRedis()

    async def unexpected(*_args: object) -> None:
        raise AssertionError("future schema must not run registered callbacks")

    result = await consume_one(
        event_factory,
        redis,
        consumer=CONSUMER,
        delivery=_delivery(event),
        revalidate=unexpected,
        apply_effect=unexpected,
    )

    assert result == "deferred"
    assert redis.calls == []
    async with event_factory() as session:
        assert await session.scalar(
            select(func.count()).select_from(InboxEvent)
        ) == 0


@pytest.mark.parametrize("tamper", ["digest", "extra_field", "missing_authority"])
async def test_untrusted_delivery_reaches_content_free_terminal_path(
    event_factory: async_sessionmaker[AsyncSession],
    tamper: str,
) -> None:
    event = await _seed_event(event_factory)
    if tamper == "missing_authority":
        event.event_id = uuid4()
        event.payload["event_id"] = str(event.event_id)
        encoded = json.dumps(
            event.payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        event.envelope_digest = hashlib.sha256(encoded).hexdigest()
    redis = RecordingRedis()

    async def unexpected(*_args: object) -> None:
        raise AssertionError("untrusted event must not run callbacks")

    result = await consume_one(
        event_factory,
        redis,
        consumer=CONSUMER,
        delivery=_delivery(
            event,
            delivery_count=3,
            digest=(b"f" * 64 if tamper == "digest" else None),
            extra_field=tamper == "extra_field",
        ),
        revalidate=unexpected,
        apply_effect=unexpected,
        poison_delivery_limit=3,
    )

    assert result == "rejected"
    assert [name for name, _ in redis.calls] == ["xadd", "waitaof", "xack"]
    dlq_stream, fields = redis.calls[0][1]  # type: ignore[misc]
    assert dlq_stream == DOCUMENT_EVENTS_DLQ_STREAM
    assert set(fields) == {
        b"error_code",
        b"source_message_id",
        b"source_stream",
    }
    assert b"SENTINEL" not in repr(redis.calls).encode()
    async with event_factory() as session:
        assert await session.scalar(
            select(func.count()).select_from(InboxEvent)
        ) == 0


class AuthoritySession:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    async def scalar(self, _statement: object) -> object:
        return self.values.pop(0)


async def test_document_revalidation_requires_exact_current_authority() -> None:
    payload = DocumentVersionLifecycleV1(
        document_id=UUID("81000000-0000-0000-0000-000000000001"),
        previous_state=DocumentVersionState.REVIEW,
        new_state=DocumentVersionState.APPROVED,
    )
    envelope = build_envelope(
        payload=payload,
        event_id=UUID("86000000-0000-0000-0000-000000000006"),
        org_id=UUID("82000000-0000-0000-0000-000000000002"),
        workspace_id=UUID("83000000-0000-0000-0000-000000000003"),
        aggregate_id=UUID("84000000-0000-0000-0000-000000000004"),
        lifecycle_revision=2,
        correlation_id=UUID("85000000-0000-0000-0000-000000000005"),
        occurred_at=datetime(2026, 7, 20, 2, tzinfo=UTC),
    )
    version = SimpleNamespace(
        document_id=payload.document_id,
        lifecycle_revision=2,
        state="approved",
    )

    await revalidate_document_lifecycle(
        AuthoritySession([envelope.org_id, envelope.workspace_id, version]),  # type: ignore[arg-type]
        envelope,
    )

    stale = SimpleNamespace(
        document_id=payload.document_id,
        lifecycle_revision=3,
        state="superseded",
    )
    with pytest.raises(RuntimeError, match="event_not_authoritative"):
        await revalidate_document_lifecycle(
            AuthoritySession([envelope.org_id, envelope.workspace_id, stale]),  # type: ignore[arg-type]
            envelope,
        )
