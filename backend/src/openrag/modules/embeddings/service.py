"""Transactional management of immutable profiles and safe deployments."""

from uuid import UUID, uuid4

from sqlalchemy import distinct, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import Settings
from openrag.core.db import naive_utc
from openrag.core.errors import ConflictError, NotFoundError
from openrag.modules.audit.service import record_audit
from openrag.modules.documents.models import (
    DocumentVersion,
    IngestStageAttempt,
)
from openrag.modules.embeddings.models import EmbeddingDeployment, EmbeddingProfile
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


async def list_deployments(session: AsyncSession) -> list[EmbeddingDeployment]:
    return list(
        (
            await session.scalars(
                select(EmbeddingDeployment).order_by(
                    EmbeddingDeployment.created_at.desc(),
                    EmbeddingDeployment.id.desc(),
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


async def get_deployment(
    session: AsyncSession,
    deployment_id: UUID,
    *,
    lock: bool = False,
) -> EmbeddingDeployment:
    statement = select(EmbeddingDeployment).where(
        EmbeddingDeployment.id == deployment_id
    )
    if lock:
        statement = statement.with_for_update()
    deployment = await session.scalar(statement)
    if deployment is None:
        raise NotFoundError("embedding deployment not found")
    return deployment


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
            if not enabled:
                governed = await session.scalar(
                    select(EmbeddingDeployment.id).where(
                        EmbeddingDeployment.profile_id == profile.id,
                        EmbeddingDeployment.status.in_(
                            ("building", "ready", "active")
                        ),
                    )
                )
                if governed is not None:
                    raise ConflictError(
                        "an active or pending embedding profile cannot be disabled"
                    )
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


async def request_deployment(
    session: AsyncSession,
    context: TenantContext,
    profile_id: UUID,
) -> EmbeddingDeployment:
    """Create one pending generation without mutating the active generation."""

    try:
        profile = await get_profile(session, profile_id, lock=True)
        if not profile.enabled:
            raise ConflictError("disabled embedding profile cannot be deployed")
        existing_pending = await session.scalar(
            select(EmbeddingDeployment.id).where(
                EmbeddingDeployment.status.in_(("building", "ready"))
            )
        )
        if existing_pending is not None:
            raise ConflictError("another embedding deployment is already pending")
        already_active = await session.scalar(
            select(EmbeddingDeployment.id).where(
                EmbeddingDeployment.profile_id == profile.id,
                EmbeddingDeployment.status == "active",
            )
        )
        if already_active is not None:
            raise ConflictError("embedding profile is already active")

        deployment = EmbeddingDeployment(
            profile_id=profile.id,
            generation_id=uuid4(),
            status="building",
            requested_by=context.user_id,
            total_versions=0,
            completed_versions=0,
            failed_versions=0,
            scan_complete=False,
        )
        session.add(deployment)
        await session.flush()
        await record_audit(
            session,
            org_id=None,
            actor_id=context.user_id,
            action="embedding_deployment.requested",
            target_type="embedding_deployment",
            target_id=str(deployment.id),
        )
        await session.commit()
        return deployment
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(
            "another embedding deployment is already pending"
        ) from exc
    except Exception:
        await session.rollback()
        raise


async def activate_deployment(
    session: AsyncSession,
    context: TenantContext,
    deployment_id: UUID,
) -> EmbeddingDeployment:
    """Atomically cut database authority over after exact corpus verification."""

    try:
        deployment = await get_deployment(session, deployment_id, lock=True)
        if deployment.status != "ready":
            raise ConflictError("embedding deployment is not ready")

        current_versions = await session.scalar(
            select(func.count())
            .select_from(DocumentVersion)
            .where(
                DocumentVersion.state == "approved",
                DocumentVersion.superseded_by_id.is_(None),
                DocumentVersion.provenance_state == "ready",
                DocumentVersion.source_deleted_at.is_(None),
                DocumentVersion.source_storage_key.is_not(None),
            )
        )
        completed_versions = await session.scalar(
            select(
                func.count(distinct(IngestStageAttempt.document_version_id))
            ).where(
                IngestStageAttempt.embedding_deployment_id == deployment.id,
                IngestStageAttempt.pipeline_kind == "reindex",
                IngestStageAttempt.stage == "authority_upsert",
                IngestStageAttempt.state == "succeeded",
            )
        )
        if (
            current_versions != deployment.total_versions
            or completed_versions != deployment.total_versions
            or deployment.completed_versions != deployment.total_versions
            or deployment.failed_versions != 0
            or not deployment.scan_complete
        ):
            deployment.status = "failed"
            deployment.failure_code = "CORPUS_CHANGED_DURING_REINDEX"
            await record_audit(
                session,
                org_id=None,
                actor_id=context.user_id,
                action="embedding_deployment.activation_rejected",
                target_type="embedding_deployment",
                target_id=str(deployment.id),
            )
            await session.commit()
            raise ConflictError("approved corpus changed during embedding reindex")

        active = await session.scalar(
            select(EmbeddingDeployment)
            .where(EmbeddingDeployment.status == "active")
            .with_for_update()
        )
        if active is not None:
            active.status = "retired"
            await session.flush()
        deployment.status = "active"
        deployment.activated_by = context.user_id
        deployment.activated_at = naive_utc()
        await record_audit(
            session,
            org_id=None,
            actor_id=context.user_id,
            action="embedding_deployment.activated",
            target_type="embedding_deployment",
            target_id=str(deployment.id),
        )
        await session.commit()
        return deployment
    except Exception:
        await session.rollback()
        raise
