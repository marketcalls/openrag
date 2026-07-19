from typing import Any

import pytest

from openrag.modules.events.readiness import (
    EventTransportNotReady,
    check_event_transport,
)
from openrag.modules.events.streams import (
    DOCUMENT_COMMANDS_GROUP,
    DOCUMENT_COMMANDS_STREAM,
    DOCUMENT_EVENTS_GROUP,
    DOCUMENT_EVENTS_STREAM,
)


class ReadyRedis:
    async def ping(self) -> bool:
        return True

    async def info(self, section: str) -> dict[str, Any]:
        assert section == "server"
        return {"redis_version": "7.4.9"}

    async def config_get(self, pattern: str) -> dict[str, str]:
        return {
            "appendonly": "yes",
            "appendfsync": "always",
        }

    async def xinfo_groups(self, name: str) -> list[dict[bytes, bytes]]:
        expected = {
            DOCUMENT_EVENTS_STREAM: DOCUMENT_EVENTS_GROUP,
            DOCUMENT_COMMANDS_STREAM: DOCUMENT_COMMANDS_GROUP,
        }
        return [{b"name": expected[name].encode()}]


async def test_event_transport_readiness_checks_durability_and_groups() -> None:
    status = await check_event_transport(ReadyRedis())

    assert status.ready is True
    assert status.redis_version == "7.4.9"
    assert status.streams_checked == 2


class UnsafePersistenceRedis(ReadyRedis):
    async def config_get(self, pattern: str) -> dict[str, str]:
        return {"appendonly": "no", "appendfsync": "everysec"}


async def test_event_transport_rejects_unsafe_persistence() -> None:
    with pytest.raises(
        EventTransportNotReady,
        match="event_transport_persistence_unsafe",
    ):
        await check_event_transport(UnsafePersistenceRedis())


class OldRedis(ReadyRedis):
    async def info(self, section: str) -> dict[str, Any]:
        return {"redis_version": "7.0.15"}


async def test_event_transport_requires_waitaof_capable_redis() -> None:
    with pytest.raises(
        EventTransportNotReady,
        match="event_transport_version_unsupported",
    ):
        await check_event_transport(OldRedis())
