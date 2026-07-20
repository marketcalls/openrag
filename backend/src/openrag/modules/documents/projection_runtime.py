"""Lease-fenced application of lifecycle eligibility to Qdrant payloads."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, cast
from uuid import UUID, uuid4

from qdrant_client import AsyncQdrantClient, models
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.config import Settings, get_settings
from openrag.core.db import build_configured_engine, build_session_factory, naive_utc
from openrag.modules.documents.authority_storage import AuthorityCollectionSpec
from openrag.modules.documents.models import DocumentVersionProjection
from openrag.modules.embeddings.models import EmbeddingDeployment, EmbeddingProfile

_MAX_ATTEMPTS = 8


class EligibilityQdrant(Protocol):
    async def set_payload(self, **kwargs: object) -> object: ...


@dataclass(frozen=True, slots=True)
class EligibilityProjectionClaim:
    projection_id: UUID
    org_id: UUID
    workspace_id: UUID
    document_version_id: UUID
    revision: int
    is_current_eligible: bool
    generation_id: UUID
    physical_collection: str
    owner: str
    lease_token: UUID


@dataclass(frozen=True, slots=True)
class EligibilityProjectionResult:
    state: str
    revision: int | None = None


async def _db_now(session: AsyncSession) -> datetime:
    now = await session.scalar(select(func.timezone("UTC", func.now())))
    if not isinstance(now, datetime):
        raise RuntimeError("database_time_unavailable")
    return now


async def claim_eligibility_projection(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    owner: str,
    lease_seconds: int = 120,
) -> EligibilityProjectionClaim | None:
    if not 1 <= len(owner) <= 200:
        raise ValueError("projection_owner_invalid")
    if not 30 <= lease_seconds <= 600:
        raise ValueError("projection_lease_invalid")

    async with session_factory.begin() as session:
        now = await _db_now(session)
        deployment = await session.scalar(
            select(EmbeddingDeployment).where(EmbeddingDeployment.status == "active")
        )
        if deployment is None:
            return None
        profile = await session.get(EmbeddingProfile, deployment.profile_id)
        if profile is None or not profile.enabled:
            return None
        row = await session.scalar(
            select(DocumentVersionProjection)
            .where(
                DocumentVersionProjection.sync_available_at <= now,
                or_(
                    DocumentVersionProjection.sync_state.in_(("queued", "retry")),
                    (
                        (DocumentVersionProjection.sync_state == "leased")
                        & (DocumentVersionProjection.sync_lease_expires_at <= now)
                    ),
                ),
            )
            .order_by(
                DocumentVersionProjection.sync_available_at,
                DocumentVersionProjection.id,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return None
        token = uuid4()
        row.sync_state = "leased"
        row.sync_attempts += 1
        row.sync_lease_owner = owner
        row.sync_lease_token = token
        row.sync_lease_expires_at = now + timedelta(seconds=lease_seconds)
        row.sync_error_code = None
        collection = AuthorityCollectionSpec(
            generation_id=deployment.generation_id,
            dense_dimension=profile.dimension,
        ).physical_collection
        return EligibilityProjectionClaim(
            projection_id=row.id,
            org_id=row.org_id,
            workspace_id=row.workspace_id,
            document_version_id=row.document_version_id,
            revision=row.applied_revision,
            is_current_eligible=row.is_current_eligible,
            generation_id=deployment.generation_id,
            physical_collection=collection,
            owner=owner,
            lease_token=token,
        )


async def apply_projection_to_qdrant(
    claim: EligibilityProjectionClaim,
    qdrant: EligibilityQdrant,
) -> None:
    """Write only bounded eligibility metadata to exact tenant/version points."""

    selector = models.FilterSelector(
        filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="tenant_id",
                    match=models.MatchValue(value=str(claim.org_id)),
                ),
                models.FieldCondition(
                    key="workspace_id",
                    match=models.MatchValue(value=str(claim.workspace_id)),
                ),
                models.FieldCondition(
                    key="document_version_id",
                    match=models.MatchValue(value=str(claim.document_version_id)),
                ),
            ]
        )
    )
    await qdrant.set_payload(
        collection_name=claim.physical_collection,
        payload={
            "is_current_approved": claim.is_current_eligible,
            "projection_revision": claim.revision,
        },
        points=selector,
        wait=True,
    )


async def complete_eligibility_projection(
    session_factory: async_sessionmaker[AsyncSession],
    claim: EligibilityProjectionClaim,
) -> EligibilityProjectionResult:
    async with session_factory.begin() as session:
        row = await session.scalar(
            select(DocumentVersionProjection)
            .where(
                DocumentVersionProjection.id == claim.projection_id,
                DocumentVersionProjection.applied_revision == claim.revision,
                DocumentVersionProjection.sync_state == "leased",
                DocumentVersionProjection.sync_lease_owner == claim.owner,
                DocumentVersionProjection.sync_lease_token == claim.lease_token,
            )
            .with_for_update()
        )
        if row is None:
            return EligibilityProjectionResult(state="lease_lost")
        row.sync_state = "applied"
        row.sync_lease_owner = None
        row.sync_lease_token = None
        row.sync_lease_expires_at = None
        row.sync_error_code = None
        row.vector_applied_generation_id = claim.generation_id
        row.vector_applied_revision = claim.revision
        row.vector_applied_at = naive_utc()
        return EligibilityProjectionResult(state="applied", revision=claim.revision)


async def release_failed_eligibility_projection(
    session_factory: async_sessionmaker[AsyncSession],
    claim: EligibilityProjectionClaim,
) -> EligibilityProjectionResult:
    async with session_factory.begin() as session:
        now = await _db_now(session)
        row = await session.scalar(
            select(DocumentVersionProjection)
            .where(
                DocumentVersionProjection.id == claim.projection_id,
                DocumentVersionProjection.applied_revision == claim.revision,
                DocumentVersionProjection.sync_state == "leased",
                DocumentVersionProjection.sync_lease_owner == claim.owner,
                DocumentVersionProjection.sync_lease_token == claim.lease_token,
            )
            .with_for_update()
        )
        if row is None:
            return EligibilityProjectionResult(state="lease_lost")
        row.sync_lease_owner = None
        row.sync_lease_token = None
        row.sync_lease_expires_at = None
        row.sync_error_code = "VECTOR_ELIGIBILITY_SYNC_FAILED"
        if row.sync_attempts >= _MAX_ATTEMPTS:
            row.sync_state = "failed"
            return EligibilityProjectionResult(state="failed", revision=claim.revision)
        row.sync_state = "retry"
        row.sync_available_at = now + timedelta(seconds=min(2 ** max(1, row.sync_attempts), 60))
        return EligibilityProjectionResult(state="retry", revision=claim.revision)


async def run_eligibility_projection_once(
    *,
    owner: str,
    settings: Settings | None = None,
) -> EligibilityProjectionResult:
    resolved = settings or get_settings()
    engine = build_configured_engine(resolved)
    session_factory = build_session_factory(engine)
    qdrant = AsyncQdrantClient(url=resolved.qdrant_url)
    try:
        claim = await claim_eligibility_projection(
            session_factory,
            owner=owner,
            lease_seconds=resolved.document_stage_lease_seconds,
        )
        if claim is None:
            return EligibilityProjectionResult(state="idle")
        try:
            await apply_projection_to_qdrant(
                claim,
                cast(EligibilityQdrant, qdrant),
            )
        except Exception:
            return await release_failed_eligibility_projection(
                session_factory,
                claim,
            )
        return await complete_eligibility_projection(session_factory, claim)
    finally:
        await qdrant.close()
        await engine.dispose()
