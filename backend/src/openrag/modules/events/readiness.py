"""Component readiness checks for the isolated durable event transport."""

from dataclasses import dataclass
from typing import Protocol

from openrag.modules.events.streams import (
    DOCUMENT_EVENTS_GROUP,
    DOCUMENT_EVENTS_STREAM,
)


class EventTransportNotReady(RuntimeError):
    """Safe, operational error whose message is a bounded machine code."""


class ReadinessRedis(Protocol):
    async def ping(self) -> object: ...

    async def info(self, section: str) -> dict[str, object]: ...

    async def config_get(self, pattern: str) -> dict[str, str]: ...

    async def xinfo_groups(
        self,
        name: str,
    ) -> list[dict[object, object]]: ...


@dataclass(frozen=True, slots=True)
class EventTransportStatus:
    ready: bool
    redis_version: str
    streams_checked: int


def _version_tuple(version: str) -> tuple[int, int]:
    try:
        major, minor, *_ = version.split(".")
        return int(major), int(minor)
    except (TypeError, ValueError) as exc:
        raise EventTransportNotReady(
            "event_transport_version_unsupported"
        ) from exc


def _group_name(group: dict[object, object]) -> str | None:
    value = group.get(b"name", group.get("name"))
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return value if isinstance(value, str) else None


async def check_event_transport(
    redis: ReadinessRedis,
) -> EventTransportStatus:
    """Verify WAITAOF support, required persistence, and consumer groups."""

    try:
        if not await redis.ping():
            raise EventTransportNotReady("event_transport_unavailable")
        info = await redis.info("server")
        version = info.get("redis_version")
        if not isinstance(version, str) or _version_tuple(version) < (7, 2):
            raise EventTransportNotReady(
                "event_transport_version_unsupported"
            )
        persistence = await redis.config_get("append*")
        if (
            persistence.get("appendonly") != "yes"
            or persistence.get("appendfsync") != "always"
        ):
            raise EventTransportNotReady(
                "event_transport_persistence_unsafe"
            )
        groups = await redis.xinfo_groups(DOCUMENT_EVENTS_STREAM)
        if DOCUMENT_EVENTS_GROUP not in {
            _group_name(group) for group in groups
        }:
            raise EventTransportNotReady("event_stream_group_missing")
    except EventTransportNotReady:
        raise
    except Exception as exc:
        raise EventTransportNotReady("event_transport_unavailable") from exc

    return EventTransportStatus(
        ready=True,
        redis_version=version,
        streams_checked=1,
    )
