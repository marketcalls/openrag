"""Tenant-safe document upload and authoritative lifecycle commands."""

import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import Settings, get_settings
from openrag.core.db import naive_utc
from openrag.core.errors import ConflictError, NotFoundError, WorkspaceAccessDenied
from openrag.core.storage import ObjectStorage, build_storage
from openrag.modules.audit.service import record_audit
from openrag.modules.documents.lifecycle import (
    LEGACY_CHUNKING_PROFILE_VERSION,
    LEGACY_EMBEDDING_PROFILE_VERSION,
    LEGACY_INDEX_PROFILE_VERSION,
    LEGACY_OCR_PROFILE_VERSION,
    LEGACY_PARSER_PROFILE_VERSION,
    LEGACY_VERSION_KEY,
    LEGACY_VERSION_LABEL,
    DocumentVersionDecision,
    DocumentVersionState,
    InvalidDocumentTransition,
    ensure_transition,
    normalize_version_label,
)
from openrag.modules.documents.models import (
    Document,
    DocumentVersion,
    DocumentVersionDecisionRecord,
    IngestJob,
)
from openrag.modules.documents.profiles import (
    IngestionProfiles,
    active_ingestion_profiles,
)
from openrag.modules.documents.uploads import QuarantinedUpload
from openrag.modules.embeddings.models import EmbeddingDeployment, EmbeddingProfile
from openrag.modules.events.envelopes import (
    DocumentVersionIngestionRequestedV1,
    DocumentVersionLifecycleV1,
)
from openrag.modules.events.outbox import add_registered_event
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import Workspace
from openrag.modules.tenancy.service import get_workspace_checked

_logger = logging.getLogger(__name__)
_DELETABLE_STATES = frozenset(
    {
        DocumentVersionState.DRAFT.value,
        DocumentVersionState.REJECTED.value,
        DocumentVersionState.FAILED.value,
    }
)


def _is_exact_legacy(version: DocumentVersion) -> bool:
    return (
        version.id == version.document_id
        and version.sequence == 1
        and version.version_label == LEGACY_VERSION_LABEL
        and version.version_key == LEGACY_VERSION_KEY
    )


@dataclass(frozen=True)
class PreparedUpload:
    org_id: UUID
    workspace_id: UUID
    document_id: UUID
    version_id: UUID
    new_document: bool
    version_label: str
    version_key: str
    filename: str
    mime: str
    data: bytes | None = field(repr=False)
    size_bytes: int
    content_hash: str = field(repr=False)
    storage_key: str = field(repr=False)
    parser_profile_version: str
    ocr_profile_version: str
    chunking_profile_version: str
    embedding_profile_version: str
    index_profile_version: str
    authority_generation_id: UUID


@dataclass(frozen=True)
class _LifecycleSnapshot:
    document_id: UUID
    workspace_id: UUID
    candidate_revision: int
    incumbent_id: UUID | None
    incumbent_revision: int | None


class _ApprovalCandidate(Protocol):
    state: str
    provenance_state: str
    source_page_count: int | None
    effective_at: datetime | None
    expires_at: datetime | None


async def _authorize_object_workspace(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    permission: str,
) -> Workspace:
    workspace = (
        await session.execute(
            select(Workspace).where(
                Workspace.id == workspace_id,
                Workspace.org_id == context.org_id,
            )
        )
    ).scalar_one_or_none()
    authorization = context.authorization
    can_read_all = authorization.has("workspace.read_all", workspace_id)
    if workspace is None or (workspace_id not in authorization.workspace_ids and not can_read_all):
        raise NotFoundError("document not found")
    if not authorization.has(permission, workspace_id):
        raise WorkspaceAccessDenied(f"requires permission: {permission}")
    return workspace


def _validate_profile(value: str | None, field: str) -> str:
    if value is None:
        raise ConflictError(f"{field} is required")
    normalized = value.strip()
    if not normalized or len(normalized) > 100:
        raise ConflictError(f"{field} must contain 1 to 100 characters")
    return normalized


async def authorize_upload_scope(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    *,
    document_id: UUID | None = None,
    version_label: str | None = None,
    filename: str,
    mime: str,
    data: bytes | None = None,
    size_bytes: int | None = None,
    content_hash: str | None = None,
    parser_profile_version: str | None = None,
    ocr_profile_version: str | None = None,
    chunking_profile_version: str | None = None,
    embedding_profile_version: str | None = None,
    index_profile_version: str | None = None,
    authority_generation_id: UUID | None = None,
) -> PreparedUpload:
    """Authorize and normalize an upload before any object-store call."""

    try:
        document: Document | None = None
        if document_id is not None:
            document = (
                await session.execute(
                    select(Document).where(
                        Document.id == document_id,
                        Document.org_id == context.org_id,
                    )
                )
            ).scalar_one_or_none()
            if document is None:
                raise NotFoundError("document not found")
            if document.workspace_id != workspace_id:
                raise NotFoundError("document not found")

        workspace = (
            await _authorize_object_workspace(session, context, workspace_id, "document.upload")
            if document is not None
            else await get_workspace_checked(session, context, workspace_id, "document.upload")
        )
        if data is not None:
            resolved_size = len(data)
            resolved_hash = hashlib.sha256(data).hexdigest()
        elif (
            size_bytes is not None
            and size_bytes > 0
            and content_hash is not None
            and len(content_hash) == 64
            and all(character in "0123456789abcdef" for character in content_hash)
        ):
            resolved_size = size_bytes
            resolved_hash = content_hash
        else:
            raise ConflictError("validated upload identity is required")
        if document is None:
            display, key = LEGACY_VERSION_LABEL, LEGACY_VERSION_KEY
            logical_id = uuid4()
            version_id = logical_id
            profiles = (
                LEGACY_PARSER_PROFILE_VERSION,
                LEGACY_OCR_PROFILE_VERSION,
                LEGACY_CHUNKING_PROFILE_VERSION,
                LEGACY_EMBEDDING_PROFILE_VERSION,
                LEGACY_INDEX_PROFILE_VERSION,
            )
            duplicate = (
                await session.execute(
                    select(Document.id).where(
                        Document.workspace_id == workspace.id,
                        Document.content_hash == resolved_hash,
                    )
                )
            ).scalar_one_or_none()
            if duplicate is not None:
                raise ConflictError(f"identical content already uploaded as document {duplicate}")
        else:
            if version_label is None:
                raise ConflictError("version label is required")
            try:
                display, key = normalize_version_label(version_label)
            except ValueError as exc:
                raise ConflictError(str(exc)) from exc
            if key == LEGACY_VERSION_KEY:
                raise ConflictError("Legacy 1 is reserved for migrated uploads")
            profiles = (
                _validate_profile(parser_profile_version, "parser profile"),
                _validate_profile(ocr_profile_version, "OCR profile"),
                _validate_profile(chunking_profile_version, "chunking profile"),
                _validate_profile(embedding_profile_version, "embedding profile"),
                _validate_profile(index_profile_version, "index profile"),
            )
            duplicate = (
                await session.execute(
                    select(DocumentVersion.id).where(
                        DocumentVersion.document_id == document.id,
                        (
                            (DocumentVersion.version_key == key)
                            | (DocumentVersion.content_hash == resolved_hash)
                        ),
                    )
                )
            ).scalar_one_or_none()
            if duplicate is not None:
                raise ConflictError("duplicate document version")
            logical_id = document.id
            version_id = uuid4()

        prepared = PreparedUpload(
            org_id=context.org_id,
            workspace_id=workspace.id,
            document_id=logical_id,
            version_id=version_id,
            new_document=document is None,
            version_label=display,
            version_key=key,
            filename=filename,
            mime=mime,
            data=data,
            size_bytes=resolved_size,
            content_hash=resolved_hash,
            storage_key=(f"{context.org_id}/{workspace.id}/{logical_id}/{version_id}/source"),
            parser_profile_version=profiles[0],
            ocr_profile_version=profiles[1],
            chunking_profile_version=profiles[2],
            embedding_profile_version=profiles[3],
            index_profile_version=profiles[4],
            authority_generation_id=(
                authority_generation_id or get_settings().authority_generation_id
            ),
        )
        await session.commit()
        return prepared
    except Exception:
        await session.rollback()
        raise


async def create_document_record(
    session: AsyncSession,
    context: TenantContext,
    prepared: PreparedUpload,
) -> Document:
    if not prepared.new_document or prepared.org_id != context.org_id:
        raise ConflictError("prepared upload does not create a logical document")
    workspace = await get_workspace_checked(
        session, context, prepared.workspace_id, "document.upload"
    )
    duplicate = (
        await session.execute(
            select(Document.id).where(
                Document.workspace_id == workspace.id,
                Document.content_hash == prepared.content_hash,
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise ConflictError(f"identical content already uploaded as document {duplicate}")
    document = Document(
        id=prepared.document_id,
        org_id=context.org_id,
        workspace_id=workspace.id,
        name=prepared.filename,
        filename=prepared.filename,
        mime=prepared.mime,
        size_bytes=prepared.size_bytes,
        content_hash=prepared.content_hash,
        storage_key=prepared.storage_key,
        created_by=context.user_id,
    )
    session.add(document)
    await session.flush()
    return document


async def create_version_record(
    session: AsyncSession,
    context: TenantContext,
    prepared: PreparedUpload,
) -> DocumentVersion:
    if prepared.org_id != context.org_id:
        raise NotFoundError("document not found")
    await _authorize_object_workspace(session, context, prepared.workspace_id, "document.upload")
    document = (
        await session.execute(
            select(Document)
            .where(
                Document.id == prepared.document_id,
                Document.org_id == context.org_id,
                Document.workspace_id == prepared.workspace_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if document is None:
        raise NotFoundError("document not found")
    duplicate = (
        await session.execute(
            select(DocumentVersion.id).where(
                DocumentVersion.document_id == document.id,
                (
                    (DocumentVersion.version_key == prepared.version_key)
                    | (DocumentVersion.content_hash == prepared.content_hash)
                ),
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise ConflictError("duplicate document version")
    maximum = await session.scalar(
        select(func.max(DocumentVersion.sequence)).where(DocumentVersion.document_id == document.id)
    )
    sequence = int(maximum or 0) + 1
    if prepared.new_document and (
        sequence != 1
        or prepared.version_id != prepared.document_id
        or prepared.version_label != LEGACY_VERSION_LABEL
    ):
        raise ConflictError("invalid legacy upload identity")
    version = DocumentVersion(
        id=prepared.version_id,
        org_id=context.org_id,
        workspace_id=document.workspace_id,
        document_id=document.id,
        sequence=sequence,
        version_label=prepared.version_label,
        version_key=prepared.version_key,
        content_hash=prepared.content_hash,
        source_filename=prepared.filename,
        source_mime=prepared.mime,
        source_size_bytes=prepared.size_bytes,
        source_storage_key=prepared.storage_key,
        source_page_count=None,
        parser_profile_version=prepared.parser_profile_version,
        ocr_profile_version=prepared.ocr_profile_version,
        chunking_profile_version=prepared.chunking_profile_version,
        embedding_profile_version=prepared.embedding_profile_version,
        index_profile_version=prepared.index_profile_version,
        state=DocumentVersionState.PROCESSING.value,
        provenance_state="none",
        created_by=context.user_id,
    )
    session.add(version)
    await session.flush()
    return version


async def create_from_upload(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    *,
    filename: str,
    mime: str,
    data: bytes,
) -> Document:
    """Preserve Legacy-1 ingestion while keeping object I/O transaction-free."""

    prepared = await authorize_upload_scope(
        session,
        context,
        workspace_id,
        filename=filename,
        mime=mime,
        data=data,
    )

    async def write_source(storage: ObjectStorage, key: str) -> None:
        if prepared.data is None:
            raise RuntimeError("in-memory upload source is missing")
        await storage.put(key, prepared.data, content_type=prepared.mime)

    document, _version = await _persist_prepared_upload(
        session,
        context,
        prepared,
        write_source,
    )
    return document


async def create_from_quarantined_upload(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    upload: QuarantinedUpload,
) -> Document:
    """Persist a validated quarantine file without loading it into memory."""

    prepared = await authorize_upload_scope(
        session,
        context,
        workspace_id,
        filename=upload.filename,
        mime=upload.mime,
        size_bytes=upload.size_bytes,
        content_hash=upload.content_hash,
    )

    async def write_source(storage: ObjectStorage, key: str) -> None:
        await storage.put_file(key, upload.path, content_type=prepared.mime)

    document, _version = await _persist_prepared_upload(
        session,
        context,
        prepared,
        write_source,
    )
    return document


async def _active_ingestion_target(
    session: AsyncSession,
    settings: Settings,
) -> tuple[IngestionProfiles, UUID]:
    configured = active_ingestion_profiles(settings)
    row = (
        await session.execute(
            select(EmbeddingDeployment, EmbeddingProfile)
            .join(
                EmbeddingProfile,
                EmbeddingProfile.id == EmbeddingDeployment.profile_id,
            )
            .where(EmbeddingDeployment.status == "active")
        )
    ).one_or_none()
    if row is None:
        return configured, settings.authority_generation_id
    deployment, profile = row
    if not profile.enabled:
        raise ConflictError("active embedding profile is unavailable")
    return (
        IngestionProfiles(
            parser_profile_version=configured.parser_profile_version,
            ocr_profile_version=configured.ocr_profile_version,
            chunking_profile_version=configured.chunking_profile_version,
            embedding_profile_version=f"embedding/v1/{profile.config_digest}",
            index_profile_version=configured.index_profile_version,
        ),
        deployment.generation_id,
    )


async def create_version_from_quarantined_upload(
    session: AsyncSession,
    context: TenantContext,
    document: Document,
    upload: QuarantinedUpload,
    *,
    version_label: str,
) -> DocumentVersion:
    """Create one controlled version using server-authoritative profile identities."""

    settings = get_settings()
    profiles, authority_generation_id = await _active_ingestion_target(
        session,
        settings,
    )
    prepared = await authorize_upload_scope(
        session,
        context,
        document.workspace_id,
        document_id=document.id,
        version_label=version_label,
        filename=upload.filename,
        mime=upload.mime,
        size_bytes=upload.size_bytes,
        content_hash=upload.content_hash,
        parser_profile_version=profiles.parser_profile_version,
        ocr_profile_version=profiles.ocr_profile_version,
        chunking_profile_version=profiles.chunking_profile_version,
        embedding_profile_version=profiles.embedding_profile_version,
        index_profile_version=profiles.index_profile_version,
        authority_generation_id=authority_generation_id,
    )

    async def write_source(storage: ObjectStorage, key: str) -> None:
        await storage.put_file(key, upload.path, content_type=prepared.mime)

    _document, version = await _persist_prepared_upload(
        session,
        context,
        prepared,
        write_source,
    )
    return version


async def _persist_prepared_upload(
    session: AsyncSession,
    context: TenantContext,
    prepared: PreparedUpload,
    write_source: Callable[[ObjectStorage, str], Awaitable[None]],
) -> tuple[Document, DocumentVersion]:
    """Perform object I/O outside SQL, then atomically record its authority."""

    storage = build_storage(get_settings())

    async def compensate() -> None:
        try:
            await storage.delete(prepared.storage_key)
        except Exception:
            _logger.error("upload object compensation failed")

    try:
        await storage.ensure_bucket()
        await write_source(storage, prepared.storage_key)
    except Exception:
        await session.rollback()
        await compensate()
        raise
    try:
        document = await create_document_record(session, context, prepared)
        version = await create_version_record(session, context, prepared)
        _persist_ingestion_request(
            session,
            version,
            prepared.authority_generation_id,
        )
        await record_audit(
            session,
            org_id=context.org_id,
            actor_id=context.user_id,
            action="document.uploaded",
            target_type="document",
            target_id=str(document.id),
        )
        await session.commit()
        return document, version
    except Exception:
        await session.rollback()
        await compensate()
        raise


async def list_documents(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
) -> list[Document]:
    workspace = await get_workspace_checked(session, context, workspace_id, "document.read")
    statement = (
        select(Document)
        .where(Document.workspace_id == workspace.id)
        .order_by(Document.created_at.desc(), Document.id.desc())
    )
    return list((await session.execute(statement)).scalars())


async def get_document_checked(
    session: AsyncSession,
    context: TenantContext,
    document_id: UUID,
    *,
    permission: str = "document.read",
) -> Document:
    document = (
        await session.execute(
            select(Document).where(
                Document.id == document_id,
                Document.org_id == context.org_id,
            )
        )
    ).scalar_one_or_none()
    if document is None:
        raise NotFoundError("document not found")
    await _authorize_object_workspace(session, context, document.workspace_id, permission)
    return document


async def patch_document(
    session: AsyncSession,
    context: TenantContext,
    document_id: UUID,
    changes: dict[str, str | None],
) -> Document:
    """Apply the bounded logical metadata fields exposed by DocumentPatch."""

    document = await get_document_checked(
        session,
        context,
        document_id,
        permission="document.upload",
    )
    try:
        for field_name, value in changes.items():
            normalized = value.strip() if isinstance(value, str) else value
            if normalized == "":
                raise ConflictError(f"{field_name} cannot be blank")
            setattr(document, field_name, normalized)
        await record_audit(
            session,
            org_id=context.org_id,
            actor_id=context.user_id,
            action="document.metadata.updated",
            target_type="document",
            target_id=str(document.id),
        )
        await session.commit()
        return document
    except Exception:
        await session.rollback()
        raise


async def get_version_checked(
    session: AsyncSession,
    context: TenantContext,
    version_id: UUID,
    *,
    permission: str = "document.read",
) -> DocumentVersion:
    version = (
        await session.execute(
            select(DocumentVersion)
            .where(
                DocumentVersion.id == version_id,
                DocumentVersion.org_id == context.org_id,
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if version is None:
        raise NotFoundError("document version not found")
    await _authorize_object_workspace(session, context, version.workspace_id, permission)
    return version


async def list_versions(
    session: AsyncSession,
    context: TenantContext,
    document_id: UUID,
    *,
    permission: str = "document.read",
) -> list[DocumentVersion]:
    document = (
        await session.execute(
            select(Document).where(
                Document.id == document_id,
                Document.org_id == context.org_id,
            )
        )
    ).scalar_one_or_none()
    if document is None:
        raise NotFoundError("document not found")
    await _authorize_object_workspace(session, context, document.workspace_id, permission)
    return list(
        (
            await session.execute(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document.id)
                .order_by(DocumentVersion.sequence.desc(), DocumentVersion.id.desc())
            )
        ).scalars()
    )


def _normalize_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    normalized = reason.strip()
    if not normalized or len(normalized) > 500:
        raise ConflictError("decision reason must contain 1 to 500 characters")
    return normalized


def _validate_approval_candidate(
    candidate: _ApprovalCandidate,
    now: datetime,
) -> None:
    if candidate.state != DocumentVersionState.REVIEW.value:
        raise ConflictError("document version is not awaiting review")
    if candidate.provenance_state != "ready":
        raise ConflictError("document version provenance is not ready")
    page_count = candidate.source_page_count
    if page_count is None or page_count <= 0:
        raise ConflictError("document version has no verified pages")
    effective_at = candidate.effective_at
    expires_at = candidate.expires_at
    if effective_at is not None and effective_at > now:
        raise ConflictError("document version is not effective")
    if expires_at is not None and expires_at <= now:
        raise ConflictError("document version is expired")


async def _capture_lifecycle_snapshot(
    session: AsyncSession,
    context: TenantContext,
    version_id: UUID,
    permission: str,
) -> _LifecycleSnapshot:
    candidate = (
        await session.execute(
            select(DocumentVersion)
            .where(
                DocumentVersion.id == version_id,
                DocumentVersion.org_id == context.org_id,
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if candidate is None:
        raise NotFoundError("document version not found")
    await _authorize_object_workspace(session, context, candidate.workspace_id, permission)
    incumbent = (
        await session.execute(
            select(DocumentVersion.id, DocumentVersion.lifecycle_revision).where(
                DocumentVersion.document_id == candidate.document_id,
                DocumentVersion.state == DocumentVersionState.APPROVED.value,
                DocumentVersion.superseded_by_id.is_(None),
            )
        )
    ).one_or_none()
    return _LifecycleSnapshot(
        document_id=candidate.document_id,
        workspace_id=candidate.workspace_id,
        candidate_revision=candidate.lifecycle_revision,
        incumbent_id=incumbent[0] if incumbent else None,
        incumbent_revision=incumbent[1] if incumbent else None,
    )


async def _lock_lifecycle_snapshot(
    session: AsyncSession,
    context: TenantContext,
    version_id: UUID,
    snapshot: _LifecycleSnapshot,
) -> tuple[DocumentVersion, DocumentVersion | None]:
    document = (
        await session.execute(
            select(Document)
            .where(
                Document.id == snapshot.document_id,
                Document.org_id == context.org_id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if document is None:
        raise ConflictError("document changed while command was waiting")
    ids = {version_id}
    if snapshot.incumbent_id is not None:
        ids.add(snapshot.incumbent_id)
    locked = list(
        (
            await session.execute(
                select(DocumentVersion)
                .where(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.id.in_(ids),
                )
                .order_by(DocumentVersion.id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalars()
    )
    by_id = {version.id: version for version in locked}
    candidate = by_id.get(version_id)
    if candidate is None or candidate.lifecycle_revision != snapshot.candidate_revision:
        raise ConflictError("document version changed while command was waiting")
    current = (
        await session.execute(
            select(DocumentVersion.id, DocumentVersion.lifecycle_revision).where(
                DocumentVersion.document_id == document.id,
                DocumentVersion.state == DocumentVersionState.APPROVED.value,
                DocumentVersion.superseded_by_id.is_(None),
            )
        )
    ).one_or_none()
    current_pair = (current[0], current[1]) if current else (None, None)
    if current_pair != (snapshot.incumbent_id, snapshot.incumbent_revision):
        raise ConflictError("approved version changed while command was waiting")
    incumbent = by_id.get(snapshot.incumbent_id) if snapshot.incumbent_id else None
    return candidate, incumbent


def _persist_decision(
    session: AsyncSession,
    version: DocumentVersion,
    actor_id: UUID,
    decision: DocumentVersionDecision,
    reason: str | None,
) -> None:
    session.add(
        DocumentVersionDecisionRecord(
            org_id=version.org_id,
            workspace_id=version.workspace_id,
            document_id=version.document_id,
            document_version_id=version.id,
            lifecycle_revision=version.lifecycle_revision,
            decision=decision.value,
            actor_id=actor_id,
            reason=reason,
        )
    )


def _persist_lifecycle_event(
    session: AsyncSession,
    version: DocumentVersion,
    previous_state: str,
    occurred_at: datetime,
) -> None:
    event = DocumentVersionLifecycleV1(
        document_id=version.document_id,
        previous_state=DocumentVersionState(previous_state),
        new_state=DocumentVersionState(version.state),
    )
    add_registered_event(
        session,
        payload=event,
        org_id=version.org_id,
        workspace_id=version.workspace_id,
        aggregate_id=version.id,
        lifecycle_revision=version.lifecycle_revision,
        occurred_at=(
            occurred_at.replace(tzinfo=UTC)
            if occurred_at.tzinfo is None
            else occurred_at.astimezone(UTC)
        ),
    )


def _persist_ingestion_request(
    session: AsyncSession,
    version: DocumentVersion,
    authority_generation_id: UUID,
) -> None:
    add_registered_event(
        session,
        payload=DocumentVersionIngestionRequestedV1(
            document_id=version.document_id,
            attempt=version.lifecycle_revision,
            authority_generation_id=authority_generation_id,
        ),
        org_id=version.org_id,
        workspace_id=version.workspace_id,
        aggregate_id=version.id,
        lifecycle_revision=version.lifecycle_revision,
        occurred_at=datetime.now(UTC),
    )


async def _generation_for_version_profile(
    session: AsyncSession,
    version: DocumentVersion,
) -> UUID:
    settings = get_settings()
    configured = active_ingestion_profiles(settings)
    if version.embedding_profile_version in {
        LEGACY_EMBEDDING_PROFILE_VERSION,
        configured.embedding_profile_version,
    }:
        return settings.authority_generation_id
    prefix = "embedding/v1/"
    if not version.embedding_profile_version.startswith(prefix):
        raise ConflictError("document embedding profile has no governed generation")
    digest = version.embedding_profile_version.removeprefix(prefix)
    deployment = await session.scalar(
        select(EmbeddingDeployment)
        .join(
            EmbeddingProfile,
            EmbeddingProfile.id == EmbeddingDeployment.profile_id,
        )
        .where(
            EmbeddingProfile.config_digest == digest,
            EmbeddingDeployment.status.in_(("active", "retired")),
        )
        .order_by(
            (EmbeddingDeployment.status == "active").desc(),
            EmbeddingDeployment.created_at.desc(),
        )
    )
    if deployment is None:
        raise ConflictError("document embedding profile has no governed generation")
    return deployment.generation_id


async def _record_lifecycle_audit(
    session: AsyncSession,
    context: TenantContext,
    version: DocumentVersion,
) -> None:
    await record_audit(
        session,
        org_id=context.org_id,
        actor_id=context.user_id,
        action=f"document.version.{version.state}",
        target_type="document_version",
        target_id=str(version.id),
    )


def _apply_transition(version: DocumentVersion, target: DocumentVersionState) -> str:
    previous = version.state
    try:
        ensure_transition(previous, target)
    except InvalidDocumentTransition as exc:
        raise ConflictError(str(exc)) from exc
    version.state = target.value
    version.lifecycle_revision += 1
    return previous


async def _has_governed_deletion_history(
    session: AsyncSession,
    version: DocumentVersion,
) -> bool:
    if version.approved_by is not None or version.approved_at is not None:
        return True
    governed_decision = await session.scalar(
        select(DocumentVersionDecisionRecord.id)
        .where(
            DocumentVersionDecisionRecord.org_id == version.org_id,
            DocumentVersionDecisionRecord.document_version_id == version.id,
            DocumentVersionDecisionRecord.decision.in_(
                (
                    DocumentVersionDecision.APPROVED.value,
                    DocumentVersionDecision.SUPERSEDED.value,
                    DocumentVersionDecision.OBSOLETE.value,
                )
            ),
        )
        .limit(1)
    )
    return governed_decision is not None


async def approve_version(
    session: AsyncSession,
    context: TenantContext,
    version_id: UUID,
    *,
    reason: str | None,
) -> DocumentVersion:
    try:
        snapshot = await _capture_lifecycle_snapshot(
            session, context, version_id, "document.approve"
        )
        normalized_reason = _normalize_reason(reason)
        candidate, incumbent = await _lock_lifecycle_snapshot(
            session, context, version_id, snapshot
        )
        now = naive_utc()
        _validate_approval_candidate(candidate, now)
        if incumbent is not None and incumbent.id == candidate.id:
            raise ConflictError("document version is already approved")
        if incumbent is not None:
            previous = _apply_transition(incumbent, DocumentVersionState.SUPERSEDED)
            incumbent.superseded_by_id = candidate.id
            incumbent.superseded_at = now
            incumbent.decision_at = now
            _persist_decision(
                session,
                incumbent,
                context.user_id,
                DocumentVersionDecision.SUPERSEDED,
                None,
            )
            _persist_lifecycle_event(session, incumbent, previous, now)
            await _record_lifecycle_audit(session, context, incumbent)
            # PostgreSQL's one-current-approved index is nondeferrable. Make
            # the incumbent ineligible before the candidate UPDATE is emitted.
            await session.flush()
        previous = _apply_transition(candidate, DocumentVersionState.APPROVED)
        candidate.approved_by = context.user_id
        candidate.approved_at = now
        candidate.decision_at = now
        _persist_decision(
            session,
            candidate,
            context.user_id,
            DocumentVersionDecision.APPROVED,
            normalized_reason,
        )
        _persist_lifecycle_event(session, candidate, previous, now)
        await _record_lifecycle_audit(session, context, candidate)
        await session.commit()
        return candidate
    except Exception:
        await session.rollback()
        raise


async def reject_version(
    session: AsyncSession,
    context: TenantContext,
    version_id: UUID,
    *,
    reason: str | None,
) -> DocumentVersion:
    try:
        snapshot = await _capture_lifecycle_snapshot(
            session, context, version_id, "document.approve"
        )
        normalized_reason = _normalize_reason(reason)
        candidate, _incumbent = await _lock_lifecycle_snapshot(
            session, context, version_id, snapshot
        )
        now = naive_utc()
        previous = _apply_transition(candidate, DocumentVersionState.REJECTED)
        candidate.rejected_by = context.user_id
        candidate.rejected_at = now
        candidate.decision_at = now
        _persist_decision(
            session,
            candidate,
            context.user_id,
            DocumentVersionDecision.REJECTED,
            normalized_reason,
        )
        _persist_lifecycle_event(session, candidate, previous, now)
        await _record_lifecycle_audit(session, context, candidate)
        await session.commit()
        return candidate
    except Exception:
        await session.rollback()
        raise


async def obsolete_version(
    session: AsyncSession,
    context: TenantContext,
    version_id: UUID,
    *,
    reason: str | None,
) -> DocumentVersion:
    try:
        snapshot = await _capture_lifecycle_snapshot(
            session, context, version_id, "document.approve"
        )
        normalized_reason = _normalize_reason(reason)
        candidate, _incumbent = await _lock_lifecycle_snapshot(
            session, context, version_id, snapshot
        )
        now = naive_utc()
        previous = _apply_transition(candidate, DocumentVersionState.OBSOLETE)
        candidate.obsolete_by = context.user_id
        candidate.obsolete_at = now
        candidate.decision_at = now
        _persist_decision(
            session,
            candidate,
            context.user_id,
            DocumentVersionDecision.OBSOLETE,
            normalized_reason,
        )
        _persist_lifecycle_event(session, candidate, previous, now)
        await _record_lifecycle_audit(session, context, candidate)
        await session.commit()
        return candidate
    except Exception:
        await session.rollback()
        raise


async def retry_version(
    session: AsyncSession,
    context: TenantContext,
    version_id: UUID,
) -> DocumentVersion:
    try:
        snapshot = await _capture_lifecycle_snapshot(
            session, context, version_id, "document.upload"
        )
        candidate, _incumbent = await _lock_lifecycle_snapshot(
            session, context, version_id, snapshot
        )
        if candidate.source_delete_requested_at is not None:
            raise ConflictError("document version deletion is already requested")
        database_now = await session.scalar(select(func.timezone("UTC", func.now())))
        if not isinstance(database_now, datetime):
            raise RuntimeError("database clock is unavailable")
        now = database_now
        if candidate.state == DocumentVersionState.PROCESSING.value:
            if not _is_exact_legacy(candidate):
                raise ConflictError("document version is already processing")
            unfinished = list(
                (
                    await session.execute(
                        select(IngestJob)
                        .where(
                            IngestJob.document_id == candidate.document_id,
                            or_(
                                IngestJob.document_version_id == candidate.id,
                                IngestJob.document_version_id.is_(None),
                            ),
                            IngestJob.finished_at.is_(None),
                        )
                        .order_by(IngestJob.id)
                        .with_for_update()
                    )
                ).scalars()
            )
            activity_times = [candidate.updated_at]
            activity_times.extend(job.started_at or job.created_at for job in unfinished)
            stale_cutoff = now - timedelta(
                seconds=max(1, get_settings().stale_ingest_recovery_seconds)
            )
            if max(activity_times) > stale_cutoff:
                raise ConflictError("ingest attempt is still active")
            previous = candidate.state
            candidate.lifecycle_revision += 1
            for job in unfinished:
                job.finished_at = now
                job.error = "superseded_by_v2_recovery"
        else:
            previous = _apply_transition(candidate, DocumentVersionState.PROCESSING)
        candidate.provenance_state = "none"
        candidate.processing_error_code = None
        if _is_exact_legacy(candidate):
            document = await session.get(Document, candidate.document_id)
            if document is None:
                raise ConflictError("document changed while command was waiting")
            document.status = "processing"
            document.error = None
        _persist_lifecycle_event(session, candidate, previous, now)
        authority_generation_id = await _generation_for_version_profile(
            session,
            candidate,
        )
        _persist_ingestion_request(
            session,
            candidate,
            authority_generation_id,
        )
        await _record_lifecycle_audit(session, context, candidate)
        await session.commit()
        return candidate
    except Exception:
        await session.rollback()
        raise


async def request_document_deletion(
    session: AsyncSession,
    context: TenantContext,
    version_id: UUID,
) -> DocumentVersion:
    try:
        snapshot = await _capture_lifecycle_snapshot(
            session, context, version_id, "document.upload"
        )
        candidate, _incumbent = await _lock_lifecycle_snapshot(
            session, context, version_id, snapshot
        )
        if candidate.state not in _DELETABLE_STATES:
            raise ConflictError("document version cannot be physically deleted")
        if await _has_governed_deletion_history(session, candidate):
            raise ConflictError("governed history cannot be physically deleted")
        if candidate.source_delete_requested_at is None:
            candidate.source_delete_requested_at = naive_utc()
            candidate.source_delete_requested_by = context.user_id
            await record_audit(
                session,
                org_id=context.org_id,
                actor_id=context.user_id,
                action="document.version.deletion_requested",
                target_type="document_version",
                target_id=str(candidate.id),
            )
        await session.commit()
        return candidate
    except Exception:
        await session.rollback()
        raise
