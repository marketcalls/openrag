"""Lease-fenced, restart-safe discovery for pending embedding generations."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from qdrant_client import AsyncQdrantClient
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.config import Settings, get_settings
from openrag.core.db import build_engine, build_session_factory, naive_utc
from openrag.modules.documents.authority_storage import (
    AuthorityCollectionSpec,
    provision_authority_storage,
)
from openrag.modules.documents.models import (
    DocumentVersion,
    DocumentVersionProjection,
)
from openrag.modules.embeddings.models import EmbeddingDeployment, EmbeddingProfile
from openrag.modules.events.envelopes import DocumentVersionReindexRequestedV1
from openrag.modules.events.outbox import add_registered_event

_MAX_PAGE_SIZE = 1_000
_MAX_SCAN_ATTEMPTS = 8


@dataclass(frozen=True, slots=True)
class DeploymentScanClaim:
    deployment_id: UUID
    generation_id: UUID
    profile_version: str
    dimension: int
    owner: str
    lease_token: UUID
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class DeploymentScanResult:
    state: str
    scanned: int = 0
    emitted: int = 0
    scan_complete: bool = False


async def _db_now(session: AsyncSession) -> datetime:
    now = await session.scalar(select(func.timezone("UTC", func.now())))
    if not isinstance(now, datetime):
        raise RuntimeError("database_time_unavailable")
    return now


async def claim_deployment_scan(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    owner: str,
    lease_seconds: int = 120,
) -> DeploymentScanClaim | None:
    if not 1 <= len(owner) <= 200:
        raise ValueError("deployment_scan_owner_invalid")
    if not 30 <= lease_seconds <= 600:
        raise ValueError("deployment_scan_lease_invalid")

    async with session_factory.begin() as session:
        now = await _db_now(session)
        deployment = await session.scalar(
            select(EmbeddingDeployment)
            .where(
                EmbeddingDeployment.status == "building",
                EmbeddingDeployment.scan_complete.is_(False),
                or_(
                    EmbeddingDeployment.lease_expires_at.is_(None),
                    EmbeddingDeployment.lease_expires_at <= now,
                ),
            )
            .order_by(
                EmbeddingDeployment.created_at,
                EmbeddingDeployment.id,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if deployment is None:
            return None
        profile = await session.get(EmbeddingProfile, deployment.profile_id)
        if profile is None or not profile.enabled:
            deployment.status = "failed"
            deployment.failure_code = "EMBEDDING_PROFILE_UNAVAILABLE"
            deployment.lease_owner = None
            deployment.lease_token = None
            deployment.lease_expires_at = None
            return None

        token = uuid4()
        expires = now + timedelta(seconds=lease_seconds)
        deployment.lease_owner = owner
        deployment.lease_token = token
        deployment.lease_expires_at = expires
        deployment.attempts += 1
        return DeploymentScanClaim(
            deployment_id=deployment.id,
            generation_id=deployment.generation_id,
            profile_version=f"embedding/v1/{profile.config_digest}",
            dimension=profile.dimension,
            owner=owner,
            lease_token=token,
            lease_expires_at=expires,
        )


async def scan_claimed_deployment_page(
    session_factory: async_sessionmaker[AsyncSession],
    claim: DeploymentScanClaim,
    *,
    page_size: int = 250,
) -> DeploymentScanResult:
    if not 1 <= page_size <= _MAX_PAGE_SIZE:
        raise ValueError("deployment_scan_page_size_invalid")

    async with session_factory.begin() as session:
        now = await _db_now(session)
        deployment = await session.scalar(
            select(EmbeddingDeployment)
            .where(
                EmbeddingDeployment.id == claim.deployment_id,
                EmbeddingDeployment.generation_id == claim.generation_id,
                EmbeddingDeployment.status == "building",
                EmbeddingDeployment.lease_owner == claim.owner,
                EmbeddingDeployment.lease_token == claim.lease_token,
                EmbeddingDeployment.lease_expires_at > now,
            )
            .with_for_update()
        )
        if deployment is None:
            return DeploymentScanResult(state="lease_lost")

        versions_query = (
            select(DocumentVersion)
            .join(
                DocumentVersionProjection,
                (
                    DocumentVersionProjection.org_id == DocumentVersion.org_id
                )
                & (
                    DocumentVersionProjection.workspace_id
                    == DocumentVersion.workspace_id
                )
                & (
                    DocumentVersionProjection.document_version_id
                    == DocumentVersion.id
                ),
            )
            .where(
                DocumentVersionProjection.is_current_eligible.is_(True),
                DocumentVersion.state == "approved",
                DocumentVersion.provenance_state == "ready",
                DocumentVersion.source_deleted_at.is_(None),
                DocumentVersion.source_storage_key.is_not(None),
            )
            .order_by(DocumentVersion.id)
            .limit(page_size)
        )
        if deployment.scan_cursor_document_version_id is not None:
            versions_query = versions_query.where(
                DocumentVersion.id > deployment.scan_cursor_document_version_id
            )
        versions = list((await session.scalars(versions_query)).all())

        for version in versions:
            add_registered_event(
                session,
                payload=DocumentVersionReindexRequestedV1(
                    document_id=version.document_id,
                    deployment_id=deployment.id,
                    embedding_profile_version=claim.profile_version,
                    authority_generation_id=deployment.generation_id,
                ),
                org_id=version.org_id,
                workspace_id=version.workspace_id,
                aggregate_id=version.id,
                lifecycle_revision=version.lifecycle_revision,
                correlation_id=deployment.id,
                occurred_at=now.replace(tzinfo=UTC),
            )

        deployment.total_versions += len(versions)
        if versions:
            deployment.scan_cursor_document_version_id = versions[-1].id
        complete = len(versions) < page_size
        if complete:
            deployment.scan_complete = True
            if (
                deployment.completed_versions == deployment.total_versions
                and deployment.failed_versions == 0
            ):
                deployment.status = "ready"
        deployment.lease_owner = None
        deployment.lease_token = None
        deployment.lease_expires_at = None
        deployment.updated_at = naive_utc()
        return DeploymentScanResult(
            state="scanned",
            scanned=len(versions),
            emitted=len(versions),
            scan_complete=complete,
        )


async def release_failed_deployment_scan(
    session_factory: async_sessionmaker[AsyncSession],
    claim: DeploymentScanClaim,
) -> str:
    async with session_factory.begin() as session:
        deployment = await session.scalar(
            select(EmbeddingDeployment)
            .where(
                EmbeddingDeployment.id == claim.deployment_id,
                EmbeddingDeployment.status == "building",
                EmbeddingDeployment.lease_owner == claim.owner,
                EmbeddingDeployment.lease_token == claim.lease_token,
            )
            .with_for_update()
        )
        if deployment is None:
            return "lease_lost"
        deployment.lease_owner = None
        deployment.lease_token = None
        deployment.lease_expires_at = None
        if deployment.attempts >= _MAX_SCAN_ATTEMPTS:
            deployment.status = "failed"
            deployment.failure_code = "AUTHORITY_PROVISIONING_FAILED"
            return "failed"
        return "retry"


async def run_deployment_scan_once(
    *,
    owner: str,
    settings: Settings | None = None,
) -> DeploymentScanResult:
    resolved = settings or get_settings()
    engine = build_engine(resolved.database_url)
    session_factory = build_session_factory(engine)
    qdrant = AsyncQdrantClient(url=resolved.qdrant_url)
    try:
        claim = await claim_deployment_scan(
            session_factory,
            owner=owner,
            lease_seconds=resolved.document_stage_lease_seconds,
        )
        if claim is None:
            return DeploymentScanResult(state="idle")
        try:
            await provision_authority_storage(
                AuthorityCollectionSpec(
                    generation_id=claim.generation_id,
                    dense_dimension=claim.dimension,
                ),
                client=qdrant,
            )
            return await scan_claimed_deployment_page(
                session_factory,
                claim,
            )
        except Exception:
            state = await release_failed_deployment_scan(session_factory, claim)
            return DeploymentScanResult(state=state)
    finally:
        await qdrant.close()
        await engine.dispose()
