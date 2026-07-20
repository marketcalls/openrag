"""Ordered, bounded, replayable public events for durable agent runs."""

from collections.abc import Mapping
from typing import Protocol, cast
from uuid import UUID, uuid4

from openrag.modules.runs.events import (
    RunEventEnvelope,
    RunEventType,
    new_run_event,
)

_APPEND_EVENT_LUA = """
local cached = redis.call('GET', KEYS[1])
if cached then
  return cached
end

local sequence = redis.call('INCR', KEYS[2])
local envelope = cjson.decode(ARGV[1])
envelope['sequence'] = sequence
local encoded = cjson.encode(envelope)
local stream_id = tostring(sequence) .. '-0'

redis.call(
  'XADD', KEYS[3], 'MAXLEN', '~', ARGV[2], stream_id,
  'event', encoded
)
redis.call('SET', KEYS[1], encoded, 'EX', ARGV[3])
redis.call('EXPIRE', KEYS[2], ARGV[3])
redis.call('EXPIRE', KEYS[3], ARGV[3])
return encoded
"""


class RunEventCursorExpired(ValueError):
    """The public replay cursor is missing, expired, or belongs elsewhere."""


StreamFieldValue = bytes | str
StreamFields = Mapping[StreamFieldValue, StreamFieldValue]
StreamEntry = tuple[StreamFieldValue, StreamFields]


class EventBusRedis(Protocol):
    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: object,
    ) -> object: ...

    async def get(self, key: str) -> object: ...

    async def xrange(
        self,
        name: str,
        min: str,
        max: str,
        count: int | None = None,
    ) -> list[StreamEntry]: ...

    async def xread(
        self,
        streams: Mapping[str, str],
        count: int | None = None,
        block: int | None = None,
    ) -> list[tuple[StreamFieldValue, list[StreamEntry]]]: ...


class RedisEventBus:
    """Use one atomic Redis script to sequence and deduplicate run events."""

    def __init__(
        self,
        redis: EventBusRedis,
        *,
        max_events: int,
        retention_seconds: int,
    ) -> None:
        if max_events < 1:
            raise ValueError("max_events must be positive")
        if retention_seconds < 1:
            raise ValueError("retention_seconds must be positive")
        self._redis = redis
        self._max_events = max_events
        self._retention_seconds = retention_seconds

    @staticmethod
    def stream_key(run_id: UUID) -> str:
        return f"openrag:run:{run_id}:events"

    @staticmethod
    def sequence_key(run_id: UUID) -> str:
        return f"openrag:run:{run_id}:seq"

    @staticmethod
    def event_key(event_id: UUID) -> str:
        return f"openrag:run:event:{event_id}"

    async def append(
        self,
        *,
        event_type: RunEventType,
        run_id: UUID,
        org_id: UUID,
        workspace_id: UUID,
        chat_id: UUID,
        payload: dict[str, object],
        event_id: UUID | None = None,
    ) -> RunEventEnvelope:
        resolved_event_id = event_id or uuid4()
        template = new_run_event(
            sequence=1,
            event_type=event_type,
            run_id=run_id,
            org_id=org_id,
            workspace_id=workspace_id,
            chat_id=chat_id,
            payload=payload,
            event_id=resolved_event_id,
        )
        encoded_template = template.model_dump_json(
            exclude_none=True,
            by_alias=True,
        ).encode("utf-8")
        result = await self._redis.eval(
            _APPEND_EVENT_LUA,
            3,
            self.event_key(resolved_event_id),
            self.sequence_key(run_id),
            self.stream_key(run_id),
            encoded_template,
            self._max_events,
            self._retention_seconds,
        )
        event = self._parse_event(result)
        expected_identity = (
            resolved_event_id,
            run_id,
            org_id,
            workspace_id,
            chat_id,
            event_type,
        )
        actual_identity = (
            event.event_id,
            event.run_id,
            event.org_id,
            event.workspace_id,
            event.chat_id,
            event.event_type,
        )
        if actual_identity != expected_identity:
            raise RuntimeError("run_event_identity_conflict")
        return event

    async def read(
        self,
        run_id: UUID,
        *,
        after_event_id: UUID | None = None,
        block_ms: int | None = None,
    ) -> list[RunEventEnvelope]:
        if block_ms is not None and block_ms < 0:
            raise ValueError("block_ms cannot be negative")
        after_sequence = await self._resolve_sequence(
            run_id,
            after_event_id,
        )
        entries: list[StreamEntry]
        if block_ms is not None:
            streams = {self.stream_key(run_id): f"{after_sequence}-0"}
            batches = await self._redis.xread(
                streams,
                count=self._max_events,
                block=block_ms,
            )
            entries = [entry for _stream, rows in batches for entry in rows]
        else:
            minimum = "-" if after_sequence == 0 else f"({after_sequence}-0"
            entries = await self._redis.xrange(
                self.stream_key(run_id),
                min=minimum,
                max="+",
                count=self._max_events,
            )

        events = [self._parse_stream_entry(entry) for entry in entries]
        events = [
            event
            for event in events
            if event.run_id == run_id and event.sequence > after_sequence
        ]
        return sorted(events, key=lambda event: event.sequence)

    async def _resolve_sequence(
        self,
        run_id: UUID,
        event_id: UUID | None,
    ) -> int:
        if event_id is None:
            return 0
        cached = await self._redis.get(self.event_key(event_id))
        if cached is None:
            raise RunEventCursorExpired("run_event_cursor_expired")
        event = self._parse_event(cached)
        if event.run_id != run_id:
            raise RunEventCursorExpired("run_event_cursor_expired")
        return event.sequence

    @classmethod
    def _parse_stream_entry(cls, entry: StreamEntry) -> RunEventEnvelope:
        _stream_id, fields = entry
        encoded = fields.get(b"event", fields.get("event"))
        if encoded is None:
            raise ValueError("run_event_contract_invalid")
        return cls._parse_event(encoded)

    @staticmethod
    def _parse_event(encoded: object) -> RunEventEnvelope:
        if isinstance(encoded, bytes | bytearray):
            raw = bytes(encoded)
        elif isinstance(encoded, str):
            raw = encoded.encode("utf-8")
        else:
            raise ValueError("run_event_contract_invalid")
        try:
            return RunEventEnvelope.model_validate_json(raw)
        except ValueError as exc:
            raise ValueError("run_event_contract_invalid") from exc


def as_event_bus_redis(redis: object) -> EventBusRedis:
    """Keep the redis-py type surface out of this transport contract."""

    return cast(EventBusRedis, redis)
