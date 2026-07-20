import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

import pytest

from openrag.modules.events.bus import RedisEventBus, RunEventCursorExpired


class InMemoryScriptRedis:
    """Small Redis-script double that exercises the public bus boundary."""

    def __init__(self) -> None:
        self.sequences: dict[str, int] = {}
        self.cached: dict[str, bytes] = {}
        self.streams: dict[str, list[tuple[bytes, dict[bytes, bytes]]]] = {}

    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: object,
    ) -> bytes:
        assert "XADD" in script
        assert numkeys == 3
        cache_key, sequence_key, stream_key = map(
            str, keys_and_args[:numkeys]
        )
        raw, max_events, _retention = keys_and_args[numkeys:]
        if cache_key in self.cached:
            return self.cached[cache_key]

        sequence = self.sequences.get(sequence_key, 0) + 1
        self.sequences[sequence_key] = sequence
        envelope = json.loads(bytes(raw))
        envelope["sequence"] = sequence
        encoded = json.dumps(
            envelope,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        entries = self.streams.setdefault(stream_key, [])
        entries.append(
            (f"{sequence}-0".encode(), {b"event": encoded})
        )
        del entries[: max(0, len(entries) - int(max_events))]
        self.cached[cache_key] = encoded
        return encoded

    async def get(self, key: str) -> bytes | None:
        return self.cached.get(key)

    async def xrange(
        self,
        name: str,
        min: str,
        max: str,
        count: int | None = None,
    ) -> list[tuple[bytes, dict[bytes, bytes]]]:
        del max
        after = 0 if min == "-" else int(min.removeprefix("(").split("-")[0])
        rows = [
            row
            for row in self.streams.get(name, [])
            if int(row[0].split(b"-")[0]) > after
        ]
        return rows[:count]

    async def xread(
        self,
        streams: Mapping[str, str],
        count: int | None = None,
        block: int | None = None,
    ) -> list[tuple[bytes, list[tuple[bytes, dict[bytes, bytes]]]]]:
        del block
        name, cursor = next(iter(streams.items()))
        after = int(cursor.split("-")[0])
        rows = [
            row
            for row in self.streams.get(name, [])
            if int(row[0].split(b"-")[0]) > after
        ][:count]
        return [(name.encode(), rows)] if rows else []


async def test_append_allocates_monotonic_sequences_and_replays() -> None:
    redis = InMemoryScriptRedis()
    bus = RedisEventBus(redis, max_events=100, retention_seconds=3600)
    ids = {
        "run_id": uuid4(),
        "org_id": uuid4(),
        "workspace_id": uuid4(),
        "chat_id": uuid4(),
    }

    first = await bus.append(event_type="run.started", payload={}, **ids)
    second = await bus.append(
        event_type="message.delta",
        payload={"delta": "hello"},
        **ids,
    )

    assert [first.sequence, second.sequence] == [1, 2]
    replay = await bus.read(ids["run_id"], after_event_id=first.event_id)
    assert [event.event_id for event in replay] == [second.event_id]


async def test_duplicate_event_id_is_not_appended_twice() -> None:
    redis = InMemoryScriptRedis()
    bus = RedisEventBus(redis, max_events=100, retention_seconds=3600)
    event_id = uuid4()
    ids = {
        "run_id": uuid4(),
        "org_id": uuid4(),
        "workspace_id": uuid4(),
        "chat_id": uuid4(),
    }

    first = await bus.append(
        event_id=event_id,
        event_type="run.started",
        payload={},
        **ids,
    )
    second = await bus.append(
        event_id=event_id,
        event_type="run.started",
        payload={},
        **ids,
    )

    assert first == second
    assert len(await bus.read(ids["run_id"])) == 1


async def test_cursor_from_another_run_is_rejected() -> None:
    redis = InMemoryScriptRedis()
    bus = RedisEventBus(redis, max_events=100, retention_seconds=3600)
    first = await bus.append(
        event_type="run.started",
        payload={},
        run_id=uuid4(),
        org_id=uuid4(),
        workspace_id=uuid4(),
        chat_id=uuid4(),
    )

    with pytest.raises(RunEventCursorExpired):
        await bus.read(uuid4(), after_event_id=first.event_id)


async def test_blocking_read_returns_control_when_no_events() -> None:
    redis = InMemoryScriptRedis()
    bus = RedisEventBus(redis, max_events=100, retention_seconds=3600)

    assert await bus.read(uuid4(), block_ms=1) == []


def test_constructor_rejects_unsafe_retention_bounds() -> None:
    redis: Any = InMemoryScriptRedis()
    with pytest.raises(ValueError, match="max_events"):
        RedisEventBus(redis, max_events=0, retention_seconds=3600)
    with pytest.raises(ValueError, match="retention_seconds"):
        RedisEventBus(redis, max_events=100, retention_seconds=0)


def test_run_keys_do_not_expose_tenant_or_prompt_data() -> None:
    run_id = UUID("10000000-0000-0000-0000-000000000001")
    event_id = UUID("20000000-0000-0000-0000-000000000002")

    assert RedisEventBus.stream_key(run_id) == (
        "openrag:run:10000000-0000-0000-0000-000000000001:events"
    )
    assert RedisEventBus.event_key(event_id) == (
        "openrag:run:event:20000000-0000-0000-0000-000000000002"
    )
