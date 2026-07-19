"""Stable Redis Stream names and the deliberately tiny wire contract."""

from typing import Protocol

from redis.exceptions import ResponseError

from openrag.modules.events.envelopes import (
    INGESTION_REQUESTED_EVENT_TYPE,
    LIFECYCLE_EVENT_TYPE,
    REBUILD_REQUESTED_EVENT_TYPE,
    REINDEX_REQUESTED_EVENT_TYPE,
)

DOCUMENT_EVENTS_STREAM = "openrag:events:documents"
DOCUMENT_EVENTS_GROUP = "openrag-document-projectors-v1"
DOCUMENT_EVENTS_DLQ_STREAM = "openrag:events:documents:dlq"
DOCUMENT_COMMANDS_STREAM = "openrag:commands:documents"
DOCUMENT_COMMANDS_GROUP = "openrag-document-starts-v1"
DOCUMENT_COMMANDS_DLQ_STREAM = "openrag:commands:documents:dlq"
EVENT_TRANSPORT_FIELDS = frozenset(
    {b"envelope_bytes", b"envelope_digest"}
)


def stream_for_event_type(event_type: str) -> str:
    """Resolve only registered schemas to bounded, namespaced streams."""

    if event_type == LIFECYCLE_EVENT_TYPE:
        return DOCUMENT_EVENTS_STREAM
    if event_type in {
        INGESTION_REQUESTED_EVENT_TYPE,
        REINDEX_REQUESTED_EVENT_TYPE,
        REBUILD_REQUESTED_EVENT_TYPE,
    }:
        return DOCUMENT_COMMANDS_STREAM
    raise ValueError("schema_not_registered")


def stream_for_aggregate_type(aggregate_type: str) -> str:
    """Route future schemas by their stable, Outbox-attested aggregate type."""

    if aggregate_type == "document_version":
        return DOCUMENT_EVENTS_STREAM
    raise ValueError("schema_not_registered")


class StreamAdminRedis(Protocol):
    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str,
        mkstream: bool,
    ) -> object: ...

    async def xinfo_groups(
        self,
        name: str,
    ) -> list[dict[object, object]]: ...


def _group_name(group: dict[object, object]) -> str | None:
    value = group.get(b"name", group.get("name"))
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return value if isinstance(value, str) else None


async def ensure_streams(redis: StreamAdminRedis) -> None:
    """Idempotently create and then verify every required consumer group."""

    topology = (
        (DOCUMENT_EVENTS_STREAM, DOCUMENT_EVENTS_GROUP),
        (DOCUMENT_COMMANDS_STREAM, DOCUMENT_COMMANDS_GROUP),
    )
    for stream, group in topology:
        try:
            await redis.xgroup_create(
                stream,
                group,
                id="0-0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise RuntimeError("event_stream_provisioning_failed") from exc

        groups = await redis.xinfo_groups(stream)
        if group not in {_group_name(item) for item in groups}:
            raise RuntimeError("event_stream_group_missing")
