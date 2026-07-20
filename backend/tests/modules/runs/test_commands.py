from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.modules.events.consumer import StreamDelivery
from openrag.modules.events.envelopes import (
    RunCancelRequestedV1,
    RunRequestedV1,
    build_envelope,
    canonical_envelope_bytes,
)
from openrag.modules.events.streams import (
    RUN_COMMANDS_GROUP,
    RUN_COMMANDS_STREAM,
)
from openrag.modules.runs import commands
from openrag.modules.runs.commands import (
    command_payload,
    consume_run_command_batch,
    parse_run_command,
)


def _envelope(payload: RunRequestedV1 | RunCancelRequestedV1) -> bytes:
    return canonical_envelope_bytes(
        build_envelope(
            payload=payload,
            event_id=uuid4(),
            org_id=uuid4(),
            workspace_id=uuid4(),
            aggregate_id=payload.run_id,
            lifecycle_revision=1,
            correlation_id=uuid4(),
            occurred_at=datetime.now(UTC),
        )
    )


def test_run_request_contract_parses_to_queue_action() -> None:
    run_id = uuid4()
    payload = RunRequestedV1(
        run_id=run_id,
        user_id=uuid4(),
        chat_id=uuid4(),
        input_message_id=uuid4(),
        client_request_id=uuid4(),
        model_id=None,
    )

    envelope = parse_run_command(_envelope(payload))
    action, parsed = command_payload(envelope)

    assert action == "queue"
    assert parsed == payload


def test_cancel_contract_parses_to_cancel_action() -> None:
    payload = RunCancelRequestedV1(run_id=uuid4(), user_id=uuid4())

    action, parsed = command_payload(parse_run_command(_envelope(payload)))

    assert action == "cancel"
    assert parsed == payload


def test_run_command_contract_rejects_prompt_content() -> None:
    with pytest.raises(ValidationError):
        RunRequestedV1.model_validate(
            {
                "run_id": str(uuid4()),
                "user_id": str(uuid4()),
                "chat_id": str(uuid4()),
                "input_message_id": str(uuid4()),
                "client_request_id": str(uuid4()),
                "model_id": None,
                "prompt": "do not transport this",
            }
        )


class BatchRedis:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def xautoclaim(self, **kwargs: object) -> object:
        self.calls.append("xautoclaim")
        assert kwargs["name"] == RUN_COMMANDS_STREAM
        assert kwargs["groupname"] == RUN_COMMANDS_GROUP
        return [
            b"0-0",
            [
                (
                    b"1700000000000-0",
                    {b"envelope_bytes": b"a", b"envelope_digest": b"b"},
                )
            ],
        ]

    async def xreadgroup(self, **kwargs: object) -> object:
        self.calls.append("xreadgroup")
        return [
            [
                RUN_COMMANDS_STREAM.encode(),
                [
                    (
                        b"1700000000001-0",
                        {
                            b"envelope_bytes": b"c",
                            b"envelope_digest": b"d",
                        },
                    )
                ],
            ]
        ]

    async def xpending_range(self, **kwargs: object) -> object:
        self.calls.append("xpending_range")
        return [
            {"message_id": b"1700000000000-0", "times_delivered": 4},
            {"message_id": b"1700000000001-0", "times_delivered": 1},
        ]

    async def xadd(self, name: str, fields: dict[bytes, bytes]) -> bytes:
        raise AssertionError((name, fields))

    async def waitaof(
        self,
        num_local: int,
        num_replicas: int,
        timeout: int,
    ) -> object:
        raise AssertionError((num_local, num_replicas, timeout))

    async def xack(
        self,
        name: str,
        groupname: str,
        *ids: str,
    ) -> object:
        raise AssertionError((name, groupname, ids))


async def test_batch_reclaims_before_fresh_and_preserves_delivery_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = BatchRedis()
    deliveries: list[StreamDelivery] = []

    async def consume(
        factory: async_sessionmaker[AsyncSession],
        consumer_redis: object,
        delivery: StreamDelivery,
    ) -> str:
        del factory, consumer_redis
        deliveries.append(delivery)
        return "processed"

    monkeypatch.setattr(commands, "consume_run_command", consume)
    factory = cast(async_sessionmaker[AsyncSession], object())

    result = await consume_run_command_batch(
        factory,
        cast(commands.RunCommandRedis, redis),
        consumer="runner-a",
        batch_size=2,
        reclaim_idle_ms=30_000,
    )

    assert redis.calls == ["xautoclaim", "xreadgroup", "xpending_range"]
    assert [delivery.delivery_count for delivery in deliveries] == [4, 1]
    assert result == {
        "claimed": 1,
        "fresh": 1,
        "processed": 2,
        "duplicate": 0,
        "pending": 0,
        "deferred": 0,
        "rejected": 0,
    }


@pytest.mark.parametrize(
    ("batch_size", "reclaim_idle_ms"),
    [(0, 30_000), (101, 30_000), (1, 29_999)],
)
async def test_batch_rejects_unbounded_configuration(
    batch_size: int,
    reclaim_idle_ms: int,
) -> None:
    with pytest.raises(ValueError, match="invalid"):
        await consume_run_command_batch(
            cast(async_sessionmaker[AsyncSession], object()),
            cast(commands.RunCommandRedis, BatchRedis()),
            consumer="runner-a",
            batch_size=batch_size,
            reclaim_idle_ms=reclaim_idle_ms,
        )
