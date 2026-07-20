from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from fastapi import Request

from openrag.api.routes.runs import replay_run_events
from openrag.core.config import Settings
from openrag.core.errors import ConflictError
from openrag.modules.runs.models import AgentRun
from openrag.modules.tenancy.authorization import AuthorizationSnapshot
from openrag.modules.tenancy.context import TenantContext


class ReleasingSession:
    def __init__(self) -> None:
        self.active = True

    async def rollback(self) -> None:
        self.active = False


class AssertReleasedRedis:
    def __init__(self, session: ReleasingSession) -> None:
        self.session = session

    async def xrange(
        self,
        name: str,
        min: str,
        max: str,
        count: int | None = None,
    ) -> list[object]:
        del name, min, max, count
        assert self.session.active is False
        return []


class FakeRequest:
    def __init__(self, redis: AssertReleasedRedis) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(event_redis=redis))

    async def is_disconnected(self) -> bool:
        return True


def _context() -> TenantContext:
    user_id = uuid4()
    org_id = uuid4()
    return TenantContext(
        user_id=user_id,
        org_id=org_id,
        authorization=AuthorizationSnapshot(
            user_id=user_id,
            org_id=org_id,
            is_platform_superadmin=False,
            org_permissions=frozenset({"chat.use"}),
            workspace_permissions={},
            workspace_ids=frozenset(),
        ),
    )


async def test_replay_releases_sql_before_redis_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = ReleasingSession()
    context = _context()
    run = SimpleNamespace(
        id=uuid4(),
        org_id=context.org_id,
        workspace_id=uuid4(),
        chat_id=uuid4(),
        status="running",
    )

    async def get_run(*args: object, **kwargs: object) -> object:
        del args, kwargs
        assert session.active is True
        return run

    monkeypatch.setattr("openrag.api.routes.runs.service.get_run", get_run)
    redis = AssertReleasedRedis(session)

    response = await replay_run_events(
        run.id,
        cast(Request, FakeRequest(redis)),
        cast(object, session),  # type: ignore[arg-type]
        Settings(_env_file=None),
        context,
        None,
    )

    assert session.active is False
    assert response.headers["cache-control"] == "no-store"
    assert response.media_type == "text/event-stream"


async def test_replay_rejects_malformed_cursor_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = ReleasingSession()
    context = _context()
    run = SimpleNamespace(
        id=uuid4(),
        org_id=context.org_id,
        workspace_id=uuid4(),
        chat_id=uuid4(),
        status="running",
    )

    async def get_run(*args: object, **kwargs: object) -> AgentRun:
        del args, kwargs
        return cast(AgentRun, run)

    monkeypatch.setattr("openrag.api.routes.runs.service.get_run", get_run)

    with pytest.raises(ConflictError, match="event cursor expired"):
        await replay_run_events(
            run.id,
            cast(Request, FakeRequest(AssertReleasedRedis(session))),
            cast(object, session),  # type: ignore[arg-type]
            Settings(_env_file=None),
            context,
            "not-a-uuid",
        )
