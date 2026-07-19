"""Transactional management of immutable embedding profiles."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import Settings
from openrag.core.errors import ConflictError, NotFoundError
from openrag.modules.audit.service import record_audit
from openrag.modules.embeddings.models import EmbeddingProfile
from openrag.modules.embeddings.schemas import (
    EmbeddingProfileCreate,
    embedding_config_digest,
)
from openrag.modules.tenancy.context import TenantContext


async def list_profiles(session: AsyncSession) -> list[EmbeddingProfile]:
    return list(
        (
            await session.scalars(
                select(EmbeddingProfile).order_by(
                    EmbeddingProfile.created_at,
                    EmbeddingProfile.id,
                )
            )
        ).all()
    )


async def get_profile(
    session: AsyncSession,
    profile_id: UUID,
    *,
    lock: bool = False,
) -> EmbeddingProfile:
    statement = select(EmbeddingProfile).where(EmbeddingProfile.id == profile_id)
    if lock:
        statement = statement.with_for_update()
    profile = await session.scalar(statement)
    if profile is None:
        raise NotFoundError("embedding profile not found")
    return profile


async def create_profile(
    session: AsyncSession,
    context: TenantContext,
    body: EmbeddingProfileCreate,
    settings: Settings,
) -> EmbeddingProfile:
    if body.provider_kind == "hash" and settings.environment not in {"dev", "test"}:
        raise ConflictError("hash embeddings are restricted to platform development")
    try:
        profile = EmbeddingProfile(
            name=body.name,
            name_key=body.name.casefold(),
            provider_kind=body.provider_kind,
            model_name=body.model_name,
            dimension=body.dimension,
            max_input_tokens=body.max_input_tokens,
            batch_size=body.batch_size,
            config_digest=embedding_config_digest(body),
            enabled=True,
            created_by=context.user_id,
        )
        session.add(profile)
        await session.flush()
        await record_audit(
            session,
            org_id=None,
            actor_id=context.user_id,
            action="embedding_profile.created",
            target_type="embedding_profile",
            target_id=str(profile.id),
        )
        await session.commit()
        return profile
    except Exception:
        await session.rollback()
        raise


async def update_profile(
    session: AsyncSession,
    context: TenantContext,
    profile_id: UUID,
    *,
    name: str | None,
    enabled: bool | None,
) -> EmbeddingProfile:
    try:
        profile = await get_profile(session, profile_id, lock=True)
        if name is not None:
            profile.name = name
            profile.name_key = name.casefold()
        if enabled is not None:
            profile.enabled = enabled
        await record_audit(
            session,
            org_id=None,
            actor_id=context.user_id,
            action="embedding_profile.updated",
            target_type="embedding_profile",
            target_id=str(profile.id),
        )
        await session.commit()
        return profile
    except Exception:
        await session.rollback()
        raise
