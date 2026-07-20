from typing import cast
from uuid import UUID

from sqlalchemy import select, true
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import Settings
from openrag.core.errors import ConflictError, InvalidRequestError, NotFoundError
from openrag.modules.audit.service import record_audit
from openrag.modules.models.models import Model
from openrag.modules.models.reasoning import ReasoningEffort
from openrag.modules.models.schemas import ModelOut, ProviderKind
from openrag.modules.secrets import service as secrets_service
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
            supports_structured_json=model.supports_structured_json,
            supports_verifier=model.supports_verifier,
            supports_reasoning=model.supports_reasoning,
            default_reasoning_effort=cast(
                ReasoningEffort,
                model.default_reasoning_effort,
            ),
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
    supports_chat_completion: bool = True,
    supports_structured_json: bool = False,
    supports_verifier: bool = False,
    supports_reasoning: bool = False,
    default_reasoning_effort: ReasoningEffort = "off",
) -> Model:
    if supports_structured_json and not supports_chat_completion:
        raise InvalidRequestError(
            "structured JSON capability requires chat capability"
        )
    if supports_verifier and not supports_structured_json:
        raise InvalidRequestError(
            "verifier capability requires structured JSON capability"
        )
    if default_reasoning_effort != "off" and not supports_reasoning:
        raise InvalidRequestError(
            "default reasoning effort requires reasoning support"
        )
    try:
        model = Model(
            litellm_model_name=litellm_model_name,
            display_name=display_name,
            provider_kind=provider_kind,
            base_url=base_url,
            supports_chat_completion=supports_chat_completion,
            supports_structured_json=supports_structured_json,
            supports_verifier=supports_verifier,
            supports_reasoning=supports_reasoning,
            default_reasoning_effort=default_reasoning_effort,
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
        if api_key is not None:
            await secrets_service.set_secret(
                session,
                actor_id=ctx.user_id,
                name=f"model:{model.id}",
                value=api_key,
                settings=settings,
                commit=False,
            )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return model


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
    supports_chat_completion: bool | None = None,
    supports_structured_json: bool | None = None,
    supports_verifier: bool | None = None,
    supports_reasoning: bool | None = None,
    default_reasoning_effort: ReasoningEffort | None = None,
) -> Model:
    try:
        model = await get_model(session, model_id)
        if display_name is not None:
            model.display_name = display_name
        if base_url is not None:
            model.base_url = base_url
        if enabled is not None:
            model.enabled = enabled
        next_supports_chat = (
            model.supports_chat_completion
            if supports_chat_completion is None
            else supports_chat_completion
        )
        next_supports_json = (
            model.supports_structured_json
            if supports_structured_json is None
            else supports_structured_json
        )
        next_supports_verifier = (
            model.supports_verifier
            if supports_verifier is None
            else supports_verifier
        )
        if next_supports_json and not next_supports_chat:
            raise InvalidRequestError(
                "structured JSON capability requires chat capability"
            )
        if next_supports_verifier and not next_supports_json:
            raise InvalidRequestError(
                "verifier capability requires structured JSON capability"
            )
        model.supports_chat_completion = next_supports_chat
        model.supports_structured_json = next_supports_json
        model.supports_verifier = next_supports_verifier
        next_supports_reasoning = (
            model.supports_reasoning
            if supports_reasoning is None
            else supports_reasoning
        )
        next_default_effort = (
            model.default_reasoning_effort
            if default_reasoning_effort is None
            else default_reasoning_effort
        )
        if next_default_effort != "off" and not next_supports_reasoning:
            raise InvalidRequestError(
                "default reasoning effort requires reasoning support"
            )
        model.supports_reasoning = next_supports_reasoning
        model.default_reasoning_effort = next_default_effort
        await record_audit(
            session,
            org_id=None,
            actor_id=ctx.user_id,
            action="model.updated",
            target_type="model",
            target_id=str(model.id),
        )
        if api_key is not None:
            await secrets_service.set_secret(
                session,
                actor_id=ctx.user_id,
                name=f"model:{model.id}",
                value=api_key,
                settings=settings,
                commit=False,
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
