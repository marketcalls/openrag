"""Composition root for isolated grounded-answer quality audits."""

from openrag.core.config import Settings, get_settings
from openrag.core.db import build_configured_engine, build_session_factory
from openrag.modules.chat.quality_runtime import run_quality_audit_once


async def execute_quality_audit_once(
    *,
    owner: str,
    settings: Settings | None = None,
) -> str:
    resolved = settings or get_settings()
    engine = build_configured_engine(resolved)
    session_factory = build_session_factory(engine)
    try:
        return await run_quality_audit_once(
            session_factory,
            resolved,
            owner=owner,
        )
    finally:
        await engine.dispose()
