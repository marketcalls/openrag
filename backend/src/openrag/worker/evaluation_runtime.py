"""Composition root for the isolated RAG evaluation worker."""

from openrag.core.config import Settings, get_settings
from openrag.core.db import build_configured_engine, build_session_factory
from openrag.modules.evaluations.runner import EvaluationTickResult, execute_evaluation_once


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
