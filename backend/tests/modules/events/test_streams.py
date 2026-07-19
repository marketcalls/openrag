import pytest

from openrag.modules.events.streams import (
    DOCUMENT_EVENTS_GROUP,
    DOCUMENT_EVENTS_STREAM,
    EVENT_TRANSPORT_FIELDS,
    ensure_streams,
    stream_for_event_type,
)


def test_document_lifecycle_events_use_the_document_stream() -> None:
    assert (
        stream_for_event_type("document.version.lifecycle.v1")
        == DOCUMENT_EVENTS_STREAM
    )


def test_transport_has_exactly_two_attested_fields() -> None:
    assert EVENT_TRANSPORT_FIELDS == frozenset(
        {b"envelope_bytes", b"envelope_digest"}
    )


class RecordingRedis:
    def __init__(self) -> None:
        self.created: list[tuple[str, str, str, bool]] = []

    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str,
        mkstream: bool,
    ) -> bool:
        self.created.append((name, groupname, id, mkstream))
        return True

    async def xinfo_groups(self, name: str) -> list[dict[bytes, bytes]]:
        assert name == DOCUMENT_EVENTS_STREAM
        return [{b"name": DOCUMENT_EVENTS_GROUP.encode()}]


async def test_stream_provisioning_is_explicit_and_verified() -> None:
    redis = RecordingRedis()

    await ensure_streams(redis)

    assert redis.created == [
        (
            DOCUMENT_EVENTS_STREAM,
            DOCUMENT_EVENTS_GROUP,
            "0-0",
            True,
        )
    ]


class MissingGroupRedis(RecordingRedis):
    async def xinfo_groups(self, name: str) -> list[dict[bytes, bytes]]:
        return []


async def test_stream_provisioning_fails_when_group_is_not_visible() -> None:
    with pytest.raises(RuntimeError, match="event_stream_group_missing"):
        await ensure_streams(MissingGroupRedis())
