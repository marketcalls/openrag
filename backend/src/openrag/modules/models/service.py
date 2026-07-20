import hashlib
from typing import cast
from uuid import UUID

from sqlalchemy import select, true
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import Settings
from openrag.core.errors import ConflictError, InvalidRequestError, NotFoundError
from openrag.modules.audit.service import record_audit
from openrag.modules.models.models import Model, ModelProbe
from openrag.modules.models.reasoning import ReasoningEffort
from openrag.modules.models.schemas import ModelOut, ModelProbeStatus, ProviderKind
from openrag.modules.secrets import service as secrets_service
from openrag.modules.secrets.models import Secret
from openrag.modules.tenancy.context import TenantContext


async def get_model(session: AsyncSession, model_id: UUID) -> Model:
    model = (
        await session.execute(select(Model).where(Model.id == model_id))
    ).scalar_one_or_none()
    if model is None:
        raise NotFoundError("model not found")
    return model


async def list_models(session: AsyncSession) -> list[Model]:
    return list(
        (
            await session.execute(select(Model).order_by(Model.created_at))
        ).scalars()
    )


async def list_enabled_models(session: AsyncSession) -> list[Model]:
    statement = (
        select(Model)
        .where(
            Model.enabled == true(),
            Model.supports_chat_completion == true(),
        )
        .order_by(Model.created_at)
    )
    return list((await session.execute(statement)).scalars())


async def _enabled_model(
    session: AsyncSession,
    model_id: UUID,
) -> Model | None:
    return (
        await session.execute(
            select(Model).where(
                Model.id == model_id,
                Model.enabled == true(),
                Model.supports_chat_completion == true(),
            )
        )
    ).scalar_one_or_none()


async def resolve_model(
    session: AsyncSession,
    *,
    requested_model_id: UUID | None,
    default_model_id: UUID | None,
) -> Model:
    if requested_model_id is not None:
        model = await _enabled_model(session, requested_model_id)
        if model is None:
            raise NotFoundError("model not found or disabled")
        return model

    if default_model_id is not None:
        model = await _enabled_model(session, default_model_id)
        if model is not None:
            return model

    raise ConflictError("no model configured for workspace")


async def to_model_out(
    session: AsyncSession,
    models: list[Model],
) -> list[ModelOut]:
    fingerprints = {
        secret.name: secret.fingerprint
        for secret in await secrets_service.list_secrets(session)
    }
    return [
        ModelOut(
            id=model.id,
            litellm_model_name=model.litellm_model_name,
            display_name=model.display_name,
            provider_kind=cast(ProviderKind, model.provider_kind),
            base_url=model.base_url,
            enabled=model.enabled,
            key_fingerprint=fingerprints.get(f"model:{model.id}"),
            supports_chat_completion=model.supports_chat_completion,
            supports_streaming=model.supports_streaming,
            supports_structured_json=model.supports_structured_json,
            supports_verifier=model.supports_verifier,
            supports_tools=model.supports_tools,
            supports_vision=model.supports_vision,
            context_window=model.context_window,
            supports_reasoning=model.supports_reasoning,
            default_reasoning_effort=cast(
                ReasoningEffort,
                model.default_reasoning_effort,
            ),
            probe_status=cast(ModelProbeStatus, model.probe_status),
            probe_revision=model.probe_revision,
            probe_latency_ms=model.probe_latency_ms,
            last_probe_error_code=model.last_probe_error_code,
            last_probed_at=model.last_probed_at,
        )
        for model in models
    ]


async def create_model(
    session: AsyncSession,
    ctx: TenantContext,
    *,
    litellm_model_name: str,
    display_name: str,
    provider_kind: ProviderKind,
    base_url: str | None,
    api_key: str | None,
    settings: Settings,
) -> Model:
    try:
        model = Model(
            litellm_model_name=litellm_model_name,
            display_name=display_name,
            provider_kind=provider_kind,
            base_url=base_url,
            supports_chat_completion=False,
            supports_streaming=False,
            supports_structured_json=False,
            supports_verifier=False,
            supports_tools=False,
            supports_vision=False,
            supports_reasoning=False,
            default_reasoning_effort="off",
        )
        session.add(model)
        await session.flush()
        await record_audit(
            session,
            org_id=None,
            actor_id=ctx.user_id,
            action="model.created",
            target_type="model",
            target_id=str(model.id),
        )
        secret: Secret | None = None
        if api_key is not None:
            secret = await secrets_service.set_secret(
                session,
                actor_id=ctx.user_id,
                name=f"model:{model.id}",
                value=api_key,
                settings=settings,
                commit=False,
            )
        await create_model_probe(
            session,
            model,
            requested_by=ctx.user_id,
            key_fingerprint=secret.fingerprint if secret is not None else None,
            increment_revision=False,
        )
        await record_audit(
            session,
            org_id=None,
            actor_id=ctx.user_id,
            action="model.probe_requested",
            target_type="model",
            target_id=str(model.id),
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return model


def _configuration_fingerprint(
    model: Model,
    *,
    key_fingerprint: str | None,
) -> str:
    value = "\x1f".join(
        (
            "model-probe-v1",
            str(model.id),
            model.provider_kind,
            model.litellm_model_name,
            model.base_url or "",
            key_fingerprint or "none",
        )
    )
    return hashlib.sha256(value.encode()).hexdigest()


def _reset_measured_capabilities(model: Model) -> None:
    model.probe_status = "pending"
    model.probe_latency_ms = None
    model.last_probe_error_code = None
    model.supports_chat_completion = False
    model.supports_streaming = False
    model.supports_structured_json = False
    model.supports_verifier = False
    model.supports_tools = False
    model.supports_vision = False
    model.supports_reasoning = False
    model.default_reasoning_effort = "off"
    model.context_window = None


async def create_model_probe(
    session: AsyncSession,
    model: Model,
    *,
    requested_by: UUID | None,
    key_fingerprint: str | None,
    increment_revision: bool,
) -> ModelProbe:
    if increment_revision:
        model.probe_revision += 1
    _reset_measured_capabilities(model)
    probe = ModelProbe(
        model_id=model.id,
        requested_by=requested_by,
        revision=model.probe_revision,
        configuration_fingerprint=_configuration_fingerprint(
            model,
            key_fingerprint=key_fingerprint,
        ),
    )
    session.add(probe)
    await session.flush()
    return probe


async def request_model_probe(
    session: AsyncSession,
    ctx: TenantContext,
    model_id: UUID,
) -> ModelProbe:
    try:
        model = await get_model(session, model_id)
        if model.probe_status == "pending":
            existing = await session.scalar(
                select(ModelProbe).where(
                    ModelProbe.model_id == model.id,
                    ModelProbe.revision == model.probe_revision,
                    ModelProbe.status.in_(("queued", "running")),
                )
            )
            if existing is not None:
                return existing
        key_fingerprint = await session.scalar(
            select(Secret.fingerprint).where(Secret.name == f"model:{model.id}")
        )
        probe = await create_model_probe(
            session,
            model,
            requested_by=ctx.user_id,
            key_fingerprint=key_fingerprint,
            increment_revision=True,
        )
        await record_audit(
            session,
            org_id=None,
            actor_id=ctx.user_id,
            action="model.probe_requested",
            target_type="model",
            target_id=str(model.id),
        )
        await session.commit()
        return probe
    except Exception:
        await session.rollback()
        raise


async def update_model(
    session: AsyncSession,
    ctx: TenantContext,
    model_id: UUID,
    *,
    display_name: str | None,
    base_url: str | None,
    enabled: bool | None,
    api_key: str | None,
    settings: Settings,
    default_reasoning_effort: ReasoningEffort | None = None,
) -> Model:
    try:
        model = await get_model(session, model_id)
        if display_name is not None:
            model.display_name = display_name
        probe_required = base_url is not None and base_url != model.base_url
        if base_url is not None:
            model.base_url = base_url
        if enabled is not None:
            model.enabled = enabled
        next_default_effort = (
            model.default_reasoning_effort
            if default_reasoning_effort is None
            else default_reasoning_effort
        )
        if next_default_effort != "off" and not model.supports_reasoning:
            raise InvalidRequestError(
                "default reasoning effort requires reasoning support"
            )
        model.default_reasoning_effort = next_default_effort
        await record_audit(
            session,
            org_id=None,
            actor_id=ctx.user_id,
            action="model.updated",
            target_type="model",
            target_id=str(model.id),
        )
        secret: Secret | None = None
        if api_key is not None:
            probe_required = True
            secret = await secrets_service.set_secret(
                session,
                actor_id=ctx.user_id,
                name=f"model:{model.id}",
                value=api_key,
                settings=settings,
                commit=False,
            )
        if probe_required:
            key_fingerprint = (
                secret.fingerprint
                if secret is not None
                else await session.scalar(
                    select(Secret.fingerprint).where(
                        Secret.name == f"model:{model.id}"
                    )
                )
            )
            await create_model_probe(
                session,
                model,
                requested_by=ctx.user_id,
                key_fingerprint=key_fingerprint,
                increment_revision=True,
            )
            await record_audit(
                session,
                org_id=None,
                actor_id=ctx.user_id,
                action="model.probe_requested",
                target_type="model",
                target_id=str(model.id),
            )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return model


async def delete_model(
    session: AsyncSession,
    ctx: TenantContext,
    model_id: UUID,
) -> None:
    model = await get_model(session, model_id)
    await session.delete(model)
    await record_audit(
        session,
        org_id=None,
        actor_id=ctx.user_id,
        action="model.deleted",
        target_type="model",
        target_id=str(model_id),
    )
    await session.commit()
    await secrets_service.delete_secret(session, name=f"model:{model_id}")
