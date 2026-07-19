import pytest

from openrag.modules.events.streams import (
    DOCUMENT_COMMANDS_GROUP,
    DOCUMENT_COMMANDS_STREAM,
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


@pytest.mark.parametrize(
    "event_type",
    [
        "document.version.ingestion_requested.v1",
        "document.version.rebuild_requested.v1",
    ],
)
def test_document_start_commands_use_the_command_stream(event_type: str) -> None:
    assert stream_for_event_type(event_type) == DOCUMENT_COMMANDS_STREAM


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
        expected = {
            DOCUMENT_EVENTS_STREAM: DOCUMENT_EVENTS_GROUP,
            DOCUMENT_COMMANDS_STREAM: DOCUMENT_COMMANDS_GROUP,
        }
        return [{b"name": expected[name].encode()}]


async def test_stream_provisioning_is_explicit_and_verified() -> None:
    redis = RecordingRedis()

    await ensure_streams(redis)

    assert redis.created == [
        (
            DOCUMENT_EVENTS_STREAM,
            DOCUMENT_EVENTS_GROUP,
            "0-0",
            True,
        ),
        (
            DOCUMENT_COMMANDS_STREAM,
            DOCUMENT_COMMANDS_GROUP,
            "0-0",
            True,
        ),
    ]


class MissingGroupRedis(RecordingRedis):
    async def xinfo_groups(self, name: str) -> list[dict[bytes, bytes]]:
        return []


async def test_stream_provisioning_fails_when_group_is_not_visible() -> None:
    with pytest.raises(RuntimeError, match="event_stream_group_missing"):
        await ensure_streams(MissingGroupRedis())
