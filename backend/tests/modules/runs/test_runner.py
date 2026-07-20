from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.config import Settings
from openrag.modules.operations.facts import RunObservation
from openrag.modules.runs import runner
from openrag.modules.runs.leases import ExhaustedRun, RunLeaseClaim
from openrag.modules.runs.reply_bridge import ReplyEventBus


class FakeLifecycle:
    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def announce_start(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def acknowledge_cancel(self, *args: object, **kwargs: object) -> bool:
        del args, kwargs
        return True

    async def announce_failure(self, *args: object, **kwargs: object) -> None:
        del args, kwargs


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
async def test_worker_projects_every_claimed_terminal_outcome(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    claim = RunLeaseClaim(
        run_id=uuid4(),
        org_id=uuid4(),
        workspace_id=uuid4(),
        chat_id=uuid4(),
        token=uuid4(),
        owner="worker-1",
        attempt=1,
        recovered=False,
    )
    projected: list[tuple[UUID, RunObservation]] = []

    async def no_cancelled(*args: object, **kwargs: object) -> None:
        del args, kwargs

    async def no_exhausted(*args: object, **kwargs: object) -> None:
        del args, kwargs

    async def claimed(*args: object, **kwargs: object) -> RunLeaseClaim:
        del args, kwargs
        return claim

    async def execute(*args: object, **kwargs: object) -> str:
        del args, kwargs
        return outcome

    async def project(
        session_factory: object,
        run_id: UUID,
        observation: RunObservation,
        **kwargs: object,
    ) -> None:
        del session_factory, kwargs
        projected.append((run_id, observation))

    monkeypatch.setattr(runner, "_next_cancelled_run_id", no_cancelled)
    monkeypatch.setattr(runner, "fail_exhausted_run", no_exhausted)
    monkeypatch.setattr(runner, "claim_next_run", claimed)
    monkeypatch.setattr(runner, "_execute_with_heartbeat", execute)
    monkeypatch.setattr(runner, "record_run_fact", project, raising=False)
    monkeypatch.setattr(runner, "RunLifecycle", FakeLifecycle)

    result = await runner.execute_queued_run_once(
        cast(async_sessionmaker[AsyncSession], object()),
        cast(ReplyEventBus, object()),
        Settings(_env_file=None),
        owner="worker-1",
    )

    assert result == outcome
    assert projected == [(claim.run_id, RunObservation())]


async def test_worker_does_not_project_a_contested_nonterminal_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = RunLeaseClaim(
        run_id=uuid4(),
        org_id=uuid4(),
        workspace_id=uuid4(),
        chat_id=uuid4(),
        token=uuid4(),
        owner="worker-1",
        attempt=1,
        recovered=False,
    )
    monkeypatch.setattr(runner, "_next_cancelled_run_id", AsyncMock(return_value=None))
    monkeypatch.setattr(runner, "fail_exhausted_run", AsyncMock(return_value=None))
    monkeypatch.setattr(runner, "claim_next_run", AsyncMock(return_value=claim))
    monkeypatch.setattr(runner, "_execute_with_heartbeat", AsyncMock(return_value="contested"))
    project = AsyncMock()
    monkeypatch.setattr(runner, "record_run_fact", project, raising=False)
    monkeypatch.setattr(runner, "RunLifecycle", FakeLifecycle)

    result = await runner.execute_queued_run_once(
        cast(async_sessionmaker[AsyncSession], object()),
        cast(ReplyEventBus, object()),
        Settings(_env_file=None),
        owner="worker-1",
    )

    assert result == "contested"
    project.assert_not_awaited()


async def test_worker_projects_preclaim_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid4()
    project = AsyncMock()
    monkeypatch.setattr(runner, "_next_cancelled_run_id", AsyncMock(return_value=run_id))
    monkeypatch.setattr(runner, "record_run_fact", project, raising=False)
    monkeypatch.setattr(runner, "RunLifecycle", FakeLifecycle)

    result = await runner.execute_queued_run_once(
        cast(async_sessionmaker[AsyncSession], object()),
        cast(ReplyEventBus, object()),
        Settings(_env_file=None),
        owner="worker-1",
    )

    assert result == "cancelled"
    assert project.await_args.args[1:] == (run_id, RunObservation())


async def test_worker_projects_retry_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exhausted = ExhaustedRun(
        run_id=uuid4(),
        org_id=uuid4(),
        workspace_id=uuid4(),
        chat_id=uuid4(),
    )
    project = AsyncMock()
    monkeypatch.setattr(runner, "_next_cancelled_run_id", AsyncMock(return_value=None))
    monkeypatch.setattr(runner, "fail_exhausted_run", AsyncMock(return_value=exhausted))
    monkeypatch.setattr(runner, "record_run_fact", project, raising=False)
    monkeypatch.setattr(runner, "RunLifecycle", FakeLifecycle)

    result = await runner.execute_queued_run_once(
        cast(async_sessionmaker[AsyncSession], object()),
        cast(ReplyEventBus, object()),
        Settings(_env_file=None),
        owner="worker-1",
    )

    assert result == "failed"
    assert project.await_args.args[1:] == (exhausted.run_id, RunObservation())


async def test_worker_attempts_independent_fact_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reconcile = AsyncMock(return_value=False)
    monkeypatch.setattr(runner, "reconcile_run_fact_once", reconcile, raising=False)
    monkeypatch.setattr(runner, "_next_cancelled_run_id", AsyncMock(return_value=None))
    monkeypatch.setattr(runner, "fail_exhausted_run", AsyncMock(return_value=None))
    monkeypatch.setattr(runner, "claim_next_run", AsyncMock(return_value=None))
    monkeypatch.setattr(runner, "RunLifecycle", FakeLifecycle)
    factory = cast(async_sessionmaker[AsyncSession], object())
    settings = Settings(_env_file=None)

    result = await runner.execute_queued_run_once(
        factory,
        cast(ReplyEventBus, object()),
        settings,
        owner="worker-1",
    )

    assert result == "idle"
    reconcile.assert_awaited_once_with(
        factory,
        environment=settings.environment,
        release=settings.release,
    )
