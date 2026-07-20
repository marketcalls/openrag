"""Composition root for the isolated model capability probe worker."""

from openrag.core.config import Settings, get_settings
from openrag.core.db import build_configured_engine, build_session_factory
from openrag.modules.models.probe_runner import ProbeTickResult, execute_model_probe_once


async def run_model_probe_once(
    *,
    owner: str,
    settings: Settings | None = None,
) -> ProbeTickResult:
    resolved = settings or get_settings()
    engine = build_configured_engine(resolved)
    session_factory = build_session_factory(engine)
    try:
        return await execute_model_probe_once(
            session_factory,
            resolved,
            owner=owner,
        )
    finally:
        await engine.dispose()
