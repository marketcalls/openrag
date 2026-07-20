"""Lease-fenced execution for durable model capability probes."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.config import Settings
from openrag.core.db import naive_utc
from openrag.modules.audit.service import record_audit
from openrag.modules.models.capability_probe import (
    CapabilityProbeResult,
    apply_probe_result,
    build_probe_claim_query,
    probe_model_capabilities,
)
from openrag.modules.models.models import Model, ModelProbe
from openrag.modules.models.service import create_model_probe
from openrag.modules.orchestration.model_gateway import (
    ModelRuntime,
    resolve_model_runtime,
)
from openrag.modules.secrets.models import Secret

ProbeTickResult = Literal["idle", "contested", "passed", "failed", "stale"]


@dataclass(frozen=True, slots=True)
class ModelProbeClaim:
    probe_id: UUID
    model_id: UUID
    revision: int
    owner: str
    token: UUID


async def _ensure_pending_probe(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Backfill one pre-probe registry row without racing parallel workers."""

    async with session_factory.begin() as session:
        model = await session.scalar(
            select(Model)
            .where(
                Model.probe_status == "pending",
                ~exists().where(
                    ModelProbe.model_id == Model.id,
                    ModelProbe.revision == Model.probe_revision,
                ),
            )
            .order_by(Model.created_at, Model.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if model is None:
            return
        key_fingerprint = await session.scalar(
            select(Secret.fingerprint).where(Secret.name == f"model:{model.id}")
        )
        await create_model_probe(
            session,
            model,
            requested_by=None,
            key_fingerprint=key_fingerprint,
            increment_revision=False,
        )


async def claim_next_model_probe(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    owner: str,
    lease_seconds: int,
) -> ModelProbeClaim | None:
    if not 1 <= len(owner) <= 200:
        raise ValueError("model probe owner is invalid")
    if not 30 <= lease_seconds <= 600:
        raise ValueError("model probe lease is invalid")
    now = naive_utc()
    async with session_factory.begin() as session:
        probe = await session.scalar(build_probe_claim_query(now))
        if probe is None:
            return None
        token = uuid4()
        probe.status = "running"
        probe.attempts += 1
        probe.lease_owner = owner
        probe.lease_token = token
        probe.lease_expires_at = now + timedelta(seconds=lease_seconds)
        probe.started_at = probe.started_at or now
        return ModelProbeClaim(
            probe_id=probe.id,
            model_id=probe.model_id,
            revision=probe.revision,
            owner=owner,
            token=token,
        )


async def _prepare_probe(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    claim: ModelProbeClaim,
) -> ModelRuntime | Literal["contested", "stale"]:
    async with session_factory() as session:
        probe = await session.scalar(
            select(ModelProbe).where(
                ModelProbe.id == claim.probe_id,
                ModelProbe.status == "running",
                ModelProbe.lease_owner == claim.owner,
                ModelProbe.lease_token == claim.token,
            )
        )
        if probe is None:
            return "contested"
        model = await session.get(Model, claim.model_id)
        if model is None:
            return "contested"
        if model.probe_revision != claim.revision:
            async with session.begin_nested():
                probe.status = "stale"
                probe.completed_at = naive_utc()
                probe.lease_owner = None
                probe.lease_token = None
                probe.lease_expires_at = None
            await session.commit()
            return "stale"
        return await resolve_model_runtime(session, model, settings)


async def _persist_probe_result(
    session_factory: async_sessionmaker[AsyncSession],
    claim: ModelProbeClaim,
    result: CapabilityProbeResult,
) -> ProbeTickResult:
    async with session_factory.begin() as session:
        probe = await session.scalar(
            select(ModelProbe)
            .where(
                ModelProbe.id == claim.probe_id,
                ModelProbe.status == "running",
                ModelProbe.lease_owner == claim.owner,
                ModelProbe.lease_token == claim.token,
            )
            .with_for_update()
        )
        if probe is None:
            return "contested"
        model = await session.scalar(
            select(Model).where(Model.id == claim.model_id).with_for_update()
        )
        if model is None:
            return "contested"
        if not apply_probe_result(model, probe, result):
            return "stale"
        await record_audit(
            session,
            org_id=None,
            actor_id=None,
            action=(
                "model.probe_passed"
                if probe.status == "passed"
                else "model.probe_failed"
            ),
            target_type="model_probe",
            target_id=str(probe.id),
        )
        return "passed" if probe.status == "passed" else "failed"


async def _fail_exhausted_probe(
    session_factory: async_sessionmaker[AsyncSession],
) -> bool:
    now = naive_utc()
    async with session_factory.begin() as session:
        probe = await session.scalar(
            select(ModelProbe)
            .where(
                ModelProbe.attempts >= 3,
                or_(
                    ModelProbe.status == "queued",
                    (
                        (ModelProbe.status == "running")
                        & (ModelProbe.lease_expires_at < now)
                    ),
                ),
            )
            .order_by(ModelProbe.created_at, ModelProbe.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if probe is None:
            return False
        model = await session.get(Model, probe.model_id)
        failure = CapabilityProbeResult(
            supports_chat_completion=False,
            supports_streaming=False,
            supports_structured_json=False,
            supports_tools=False,
            supports_vision=False,
            supports_reasoning=False,
            context_window=None,
            latency_ms=0,
            error_code="probe_retry_exhausted",
        )
        if model is None:
            probe.status = "failed"
            probe.error_code = "probe_retry_exhausted"
            probe.completed_at = now
            probe.lease_owner = None
            probe.lease_token = None
            probe.lease_expires_at = None
        else:
            apply_probe_result(model, probe, failure)
        return True


async def execute_model_probe_once(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    owner: str,
) -> ProbeTickResult:
    await _ensure_pending_probe(session_factory)
    if await _fail_exhausted_probe(session_factory):
        return "failed"
    claim = await claim_next_model_probe(
        session_factory,
        owner=owner,
        lease_seconds=settings.model_probe_lease_seconds,
    )
    if claim is None:
        return "idle"
    try:
        prepared = await _prepare_probe(session_factory, settings, claim)
    except Exception:  # noqa: BLE001 - configuration details must stay private
        return await _persist_probe_result(
            session_factory,
            claim,
            CapabilityProbeResult(
                supports_chat_completion=False,
                supports_streaming=False,
                supports_structured_json=False,
                supports_tools=False,
                supports_vision=False,
                supports_reasoning=False,
                context_window=None,
                latency_ms=0,
                error_code="model_configuration_invalid",
            ),
        )
    if isinstance(prepared, str):
        return prepared
    result = await probe_model_capabilities(prepared)
    return await _persist_probe_result(session_factory, claim, result)
