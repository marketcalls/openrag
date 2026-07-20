"""Composition root for the isolated RAG evaluation worker."""

from openrag.core.config import Settings, get_settings
from openrag.core.db import build_configured_engine, build_session_factory
from openrag.modules.evaluations.runner import EvaluationTickResult, execute_evaluation_once
from openrag.modules.evaluations.service import schedule_due_policies


async def run_evaluation_once(
    *,
    owner: str,
    settings: Settings | None = None,
) -> EvaluationTickResult:
    resolved = settings or get_settings()
    engine = build_configured_engine(resolved)
    session_factory = build_session_factory(engine)
    try:
        return await execute_evaluation_once(
            session_factory,
            resolved,
            owner=owner,
        )
    finally:
        await engine.dispose()


async def run_evaluation_scheduler_once(
    *,
    settings: Settings | None = None,
) -> int:
    resolved = settings or get_settings()
    engine = build_configured_engine(resolved)
    session_factory = build_session_factory(engine)
    try:
        async with session_factory() as session:
            return await schedule_due_policies(session)
    finally:
        await engine.dispose()
