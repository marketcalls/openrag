from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk, naive_utc
from openrag.modules.documents.lifecycle import (
    DocumentVersionState,
    ProvenanceState,
)


class Document(UUIDPk, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "content_hash",
            name="uq_documents_workspace_hash",
        ),
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_documents_org_workspace_id",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_documents_org_workspace",
        ),
        ForeignKeyConstraint(
            ["org_id", "owner_id"],
            ["users.org_id", "users.id"],
            name="fk_documents_org_owner",
        ),
        ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["users.org_id", "users.id"],
            name="fk_documents_org_creator",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    filename: Mapped[str]
    mime: Mapped[str]
    size_bytes: Mapped[int]
    content_hash: Mapped[str]
    status: Mapped[str] = mapped_column(default="queued")
    error: Mapped[str | None] = mapped_column(default=None)
    storage_key: Mapped[str]
    page_count: Mapped[int | None] = mapped_column(default=None)
    owner_id: Mapped[UUID | None] = mapped_column(default=None)
    created_by: Mapped[UUID]
    updated_at: Mapped[datetime] = mapped_column(default=naive_utc, onupdate=naive_utc)


class DocumentVersion(UUIDPk, Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_document_versions_org_id"),
        UniqueConstraint(
            "org_id", "document_id", "id", name="uq_document_versions_org_document_id"
        ),
        UniqueConstraint("document_id", "id", name="uq_document_versions_document_id"),
        UniqueConstraint(
            "document_id", "sequence", name="uq_document_versions_document_sequence"
        ),
        UniqueConstraint(
            "document_id", "version_key", name="uq_document_versions_document_key"
        ),
        UniqueConstraint(
            "document_id", "content_hash", name="uq_document_versions_document_hash"
        ),
        CheckConstraint("sequence > 0", name="ck_document_versions_sequence_positive"),
        CheckConstraint(
            "lifecycle_revision >= 1",
            name="ck_document_versions_lifecycle_revision_positive",
        ),
        CheckConstraint(
            "state IN ('draft','processing','review','approved','rejected',"
            "'superseded','obsolete','failed')",
            name="ck_document_versions_state",
        ),
        CheckConstraint(
            "provenance_state IN ('none','legacy_pending','building','ready','failed')",
            name="ck_document_versions_provenance_state",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "document_id"],
            ["documents.org_id", "documents.workspace_id", "documents.id"],
            name="fk_document_versions_org_workspace_document",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "superseded_by_id"],
            ["document_versions.document_id", "document_versions.id"],
            name="fk_document_versions_same_document_successor",
        ),
        ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["users.org_id", "users.id"],
            name="fk_document_versions_org_creator",
        ),
        ForeignKeyConstraint(
            ["org_id", "approved_by"],
            ["users.org_id", "users.id"],
            name="fk_document_versions_org_approver",
        ),
        ForeignKeyConstraint(
            ["org_id", "rejected_by"],
            ["users.org_id", "users.id"],
            name="fk_document_versions_org_rejector",
        ),
        ForeignKeyConstraint(
            ["org_id", "obsolete_by"],
            ["users.org_id", "users.id"],
            name="fk_document_versions_org_obsolete_actor",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    document_id: Mapped[UUID] = mapped_column(index=True)
    sequence: Mapped[int]
    version_label: Mapped[str] = mapped_column(String(200))
    version_key: Mapped[str] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(default=DocumentVersionState.DRAFT.value, index=True)
    provenance_state: Mapped[str] = mapped_column(default=ProvenanceState.NONE.value, index=True)
    lifecycle_revision: Mapped[int] = mapped_column(default=1)
    superseded_by_id: Mapped[UUID | None] = mapped_column(default=None)
    effective_at: Mapped[datetime | None] = mapped_column(default=None)
    expires_at: Mapped[datetime | None] = mapped_column(default=None)
    created_by: Mapped[UUID]
    approved_by: Mapped[UUID | None] = mapped_column(default=None)
    rejected_by: Mapped[UUID | None] = mapped_column(default=None)
    obsolete_by: Mapped[UUID | None] = mapped_column(default=None)
    updated_at: Mapped[datetime] = mapped_column(default=naive_utc, onupdate=naive_utc)


class DocumentBlock(UUIDPk, Base):
    __tablename__ = "document_blocks"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "document_version_id", "id", name="uq_document_blocks_org_version_id"
        ),
        UniqueConstraint(
            "document_version_id", "ordinal", name="uq_document_blocks_version_ordinal"
        ),
        CheckConstraint("ordinal >= 0", name="ck_document_blocks_ordinal"),
        ForeignKeyConstraint(
            ["org_id", "document_version_id"],
            ["document_versions.org_id", "document_versions.id"],
            name="fk_document_blocks_org_version",
            ondelete="CASCADE",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    document_version_id: Mapped[UUID] = mapped_column(index=True)
    ordinal: Mapped[int]
    text: Mapped[str] = mapped_column(Text())


class DocumentChunk(UUIDPk, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "document_version_id", "id", name="uq_document_chunks_org_version_id"
        ),
        UniqueConstraint(
            "document_version_id", "ordinal", name="uq_document_chunks_version_ordinal"
        ),
        CheckConstraint("ordinal >= 0", name="ck_document_chunks_ordinal"),
        CheckConstraint("token_count >= 0", name="ck_document_chunks_token_count"),
        ForeignKeyConstraint(
            ["org_id", "document_version_id"],
            ["document_versions.org_id", "document_versions.id"],
            name="fk_document_chunks_org_version",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "document_version_id", "parent_chunk_id"],
            ["document_chunks.org_id", "document_chunks.document_version_id", "document_chunks.id"],
            name="fk_document_chunks_same_version_parent",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    document_version_id: Mapped[UUID] = mapped_column(index=True)
    parent_chunk_id: Mapped[UUID | None] = mapped_column(default=None)
    ordinal: Mapped[int]
    text: Mapped[str] = mapped_column(Text())
    token_count: Mapped[int]


class DocumentChunkBlock(UUIDPk, Base):
    __tablename__ = "document_chunk_blocks"
    __table_args__ = (
        UniqueConstraint("chunk_id", "block_id", name="uq_document_chunk_blocks_membership"),
        UniqueConstraint("chunk_id", "position", name="uq_document_chunk_blocks_position"),
        CheckConstraint("position >= 0", name="ck_document_chunk_blocks_position"),
        ForeignKeyConstraint(
            ["org_id", "document_version_id", "chunk_id"],
            ["document_chunks.org_id", "document_chunks.document_version_id", "document_chunks.id"],
            name="fk_document_chunk_blocks_same_version_chunk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "document_version_id", "block_id"],
            ["document_blocks.org_id", "document_blocks.document_version_id", "document_blocks.id"],
            name="fk_document_chunk_blocks_same_version_block",
            ondelete="CASCADE",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    document_version_id: Mapped[UUID] = mapped_column(index=True)
    chunk_id: Mapped[UUID] = mapped_column(index=True)
    block_id: Mapped[UUID] = mapped_column(index=True)
    position: Mapped[int]


class DocumentEvidenceSpan(UUIDPk, Base):
    __tablename__ = "document_evidence_spans"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "document_version_id",
            "id",
            name="uq_document_evidence_spans_org_version_id",
        ),
        UniqueConstraint(
            "document_version_id", "ordinal", name="uq_document_evidence_spans_version_ordinal"
        ),
        CheckConstraint("page_number > 0", name="ck_document_evidence_spans_page_positive"),
        CheckConstraint("ordinal >= 0", name="ck_document_evidence_spans_ordinal"),
        CheckConstraint("token_count >= 0", name="ck_document_evidence_spans_token_count"),
        CheckConstraint(
            "artifact_byte_start >= 0 AND artifact_byte_end >= artifact_byte_start",
            name="ck_document_evidence_spans_artifact_range",
        ),
        ForeignKeyConstraint(
            ["org_id", "document_version_id", "chunk_id"],
            ["document_chunks.org_id", "document_chunks.document_version_id", "document_chunks.id"],
            name="fk_document_evidence_spans_same_version_chunk",
            ondelete="CASCADE",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    document_version_id: Mapped[UUID] = mapped_column(index=True)
    chunk_id: Mapped[UUID] = mapped_column(index=True)
    page_number: Mapped[int]
    locator_kind: Mapped[str] = mapped_column(String(32))
    locator_label: Mapped[str] = mapped_column(String(200))
    section_path: Mapped[list[str]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    ordinal: Mapped[int]
    token_count: Mapped[int]
    artifact_byte_start: Mapped[int]
    artifact_byte_end: Mapped[int]


class DocumentVersionProjection(UUIDPk, Base):
    __tablename__ = "document_version_projections"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "document_version_id", name="uq_document_version_projections_version"
        ),
        ForeignKeyConstraint(
            ["org_id", "document_version_id"],
            ["document_versions.org_id", "document_versions.id"],
            name="fk_document_version_projections_org_version",
            ondelete="CASCADE",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    document_version_id: Mapped[UUID] = mapped_column(index=True)
    is_current_eligible: Mapped[bool] = mapped_column(default=False, index=True)
    lifecycle_revision: Mapped[int]
    projected_at: Mapped[datetime] = mapped_column(default=naive_utc)


class DocumentAuthorityReadiness(UUIDPk, Base):
    __tablename__ = "document_authority_readiness"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "generation_id",
            name="uq_document_authority_readiness_generation",
        ),
        CheckConstraint(
            "status IN ('building','passed','stale','failed','activated')",
            name="ck_document_authority_readiness_status",
        ),
        CheckConstraint(
            "current_version_count >= 0 AND ready_version_count >= 0 "
            "AND projected_version_count >= 0 AND point_count >= 0",
            name="ck_document_authority_readiness_counts",
        ),
        CheckConstraint("attempts >= 0", name="ck_document_authority_readiness_attempts"),
        ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_document_authority_readiness_org_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "activated_by"],
            ["users.org_id", "users.id"],
            name="fk_document_authority_readiness_org_activator",
        ),
    )

    generation_id: Mapped[UUID]
    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    request_digest: Mapped[str] = mapped_column(String(64))
    physical_collection: Mapped[str] = mapped_column(String(200))
    collection_alias: Mapped[str] = mapped_column(String(200))
    schema_version: Mapped[int]
    current_version_count: Mapped[int] = mapped_column(default=0)
    ready_version_count: Mapped[int] = mapped_column(default=0)
    projected_version_count: Mapped[int] = mapped_column(default=0)
    point_count: Mapped[int] = mapped_column(default=0)
    payload_index_digest: Mapped[str | None] = mapped_column(String(64), default=None)
    provenance_digest: Mapped[str | None] = mapped_column(String(64), default=None)
    lifecycle_revision_digest: Mapped[str | None] = mapped_column(String(64), default=None)
    grounding_policy_id: Mapped[UUID | None] = mapped_column(default=None)
    calibration_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    verifier_model_id: Mapped[UUID | None] = mapped_column(default=None)
    provider_preset_version: Mapped[str | None] = mapped_column(String(100), default=None)
    binding_revision: Mapped[str | None] = mapped_column(String(100), default=None)
    credential_fingerprint: Mapped[str | None] = mapped_column(String(128), default=None)
    readiness_digest: Mapped[str | None] = mapped_column(String(64), default=None)
    signature: Mapped[str | None] = mapped_column(String(128), default=None)
    status: Mapped[str] = mapped_column(default="building", index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(200), default=None)
    lease_expires_at: Mapped[datetime | None] = mapped_column(default=None)
    attempts: Mapped[int] = mapped_column(default=0)
    checked_at: Mapped[datetime | None] = mapped_column(default=None)
    expires_at: Mapped[datetime]
    activated_at: Mapped[datetime | None] = mapped_column(default=None)
    activated_by: Mapped[UUID | None] = mapped_column(default=None)


class IngestJob(UUIDPk, Base):
    __tablename__ = "ingest_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "document_id", "document_version_id"],
            ["document_versions.org_id", "document_versions.document_id", "document_versions.id"],
            name="fk_ingest_jobs_org_document_version",
        ),
    )

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    org_id: Mapped[UUID | None] = mapped_column(default=None, index=True)
    document_version_id: Mapped[UUID | None] = mapped_column(default=None, index=True)
    stage: Mapped[str]
    progress: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[str | None] = mapped_column(default=None)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class IngestStageAttempt(UUIDPk, Base):
    __tablename__ = "ingest_stage_attempts"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "pipeline_kind",
            "stage",
            "checkpoint",
            name="uq_ingest_stage_attempts_checkpoint",
        ),
        CheckConstraint("attempts >= 0", name="ck_ingest_stage_attempts_attempts"),
        ForeignKeyConstraint(
            ["org_id", "document_version_id"],
            ["document_versions.org_id", "document_versions.id"],
            name="fk_ingest_stage_attempts_org_version",
            ondelete="CASCADE",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    document_version_id: Mapped[UUID] = mapped_column(index=True)
    pipeline_kind: Mapped[str] = mapped_column(String(32))
    stage: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(default="queued", index=True)
    checkpoint: Mapped[str] = mapped_column(String(128))
    lease_owner: Mapped[str | None] = mapped_column(String(200), default=None)
    lease_expires_at: Mapped[datetime | None] = mapped_column(default=None)
    attempts: Mapped[int] = mapped_column(default=0)
    error_code: Mapped[str | None] = mapped_column(String(100), default=None)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class LegacyRebuildScanCheckpoint(UUIDPk, Base):
    __tablename__ = "legacy_rebuild_scan_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "workspace_id", name="uq_legacy_rebuild_scan_checkpoints_workspace"
        ),
        CheckConstraint(
            "pass_number >= 0 AND scanned_count >= 0 AND emitted_count >= 0 AND skipped_count >= 0",
            name="ck_legacy_rebuild_scan_checkpoints_counts",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_legacy_rebuild_scan_checkpoints_org_workspace",
            ondelete="CASCADE",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    cursor_document_id: Mapped[UUID | None] = mapped_column(default=None)
    pass_number: Mapped[int] = mapped_column(default=0)
    scanned_count: Mapped[int] = mapped_column(default=0)
    emitted_count: Mapped[int] = mapped_column(default=0)
    skipped_count: Mapped[int] = mapped_column(default=0)
    pass_started_at: Mapped[datetime | None] = mapped_column(default=None)
    completed_at: Mapped[datetime | None] = mapped_column(default=None)
    updated_at: Mapped[datetime] = mapped_column(default=naive_utc, onupdate=naive_utc)
