from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, validates

import openrag.modules.grounding.models  # noqa: F401 - registers readiness FK target
from openrag.core.db import Base, UUIDPk, naive_utc
from openrag.modules.documents.lifecycle import (
    DocumentVersionState,
    ProvenanceState,
    validate_section_path,
)


class Document(UUIDPk, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_documents_org_workspace_id",
        ),
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "external_identifier",
            name="uq_documents_org_workspace_external_identifier",
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
        CheckConstraint(
            "acl_policy IS NULL OR "
            "(jsonb_typeof(acl_policy) = 'object' AND pg_column_size(acl_policy) <= 8192)",
            name="ck_documents_acl_policy",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(255))
    department: Mapped[str | None] = mapped_column(String(120), default=None)
    document_type: Mapped[str | None] = mapped_column(String(120), default=None)
    external_identifier: Mapped[str | None] = mapped_column(String(255), default=None)
    acl_policy: Mapped[dict[str, object] | None] = mapped_column(JSONB, default=None)
    # Transitional mirrors for the pre-authority single-blob API. New source
    # identity belongs to DocumentVersion and Task 2 backfills these fields.
    filename: Mapped[str | None] = mapped_column(String(500), default=None)
    mime: Mapped[str | None] = mapped_column(String(255), default=None)
    size_bytes: Mapped[int | None] = mapped_column(default=None)
    content_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    status: Mapped[str | None] = mapped_column(default="queued", nullable=True)
    error: Mapped[str | None] = mapped_column(default=None)
    storage_key: Mapped[str | None] = mapped_column(String(1024), default=None)
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
        UniqueConstraint(
            "org_id",
            "document_id",
            "id",
            "version_label",
            name="uq_document_versions_org_document_id_label",
        ),
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "id",
            name="uq_document_versions_org_workspace_id",
        ),
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "document_id",
            "id",
            name="uq_document_versions_org_workspace_document_id",
        ),
        UniqueConstraint("document_id", "id", name="uq_document_versions_document_id"),
        UniqueConstraint("document_id", "sequence", name="uq_document_versions_document_sequence"),
        UniqueConstraint("document_id", "version_key", name="uq_document_versions_document_key"),
        UniqueConstraint("document_id", "content_hash", name="uq_document_versions_document_hash"),
        Index(
            "uq_document_versions_one_approved",
            "document_id",
            unique=True,
            postgresql_where=text("state='approved' AND superseded_by_id IS NULL"),
        ),
        CheckConstraint("sequence > 0", name="ck_document_versions_sequence_positive"),
        CheckConstraint(
            "lifecycle_revision >= 1",
            name="ck_document_versions_lifecycle_revision_positive",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_document_versions_content_hash_sha256",
        ),
        CheckConstraint(
            "source_size_bytes IS NULL OR source_size_bytes >= 0",
            name="ck_document_versions_source_size",
        ),
        CheckConstraint(
            "source_page_count IS NULL OR source_page_count > 0",
            name="ck_document_versions_source_page_count",
        ),
        CheckConstraint(
            "source_filename IS NOT NULL AND source_mime IS NOT NULL "
            "AND source_size_bytes IS NOT NULL AND source_storage_key IS NOT NULL",
            name="ck_document_versions_source_identity_complete",
        ),
        CheckConstraint(
            "parser_profile_version IS NOT NULL "
            "AND ocr_profile_version IS NOT NULL "
            "AND chunking_profile_version IS NOT NULL "
            "AND embedding_profile_version IS NOT NULL "
            "AND index_profile_version IS NOT NULL "
            "AND char_length(parser_profile_version) BETWEEN 1 AND 100 "
            "AND char_length(ocr_profile_version) BETWEEN 1 AND 100 "
            "AND char_length(chunking_profile_version) BETWEEN 1 AND 100 "
            "AND char_length(embedding_profile_version) BETWEEN 1 AND 100 "
            "AND char_length(index_profile_version) BETWEEN 1 AND 100",
            name="ck_document_versions_profile_snapshot_complete",
        ),
        CheckConstraint(
            "(sequence = 1 AND version_label = 'Legacy 1' AND version_key = 'legacy 1' "
            "AND parser_profile_version = 'legacy/parser-v1' "
            "AND ocr_profile_version = 'legacy/ocr-unknown-v1' "
            "AND chunking_profile_version = 'legacy/chunking-v1' "
            "AND embedding_profile_version = 'legacy/embedding-v1' "
            "AND index_profile_version = 'legacy/index-v1' "
            "AND ((state = 'approved' AND provenance_state IN "
            "('legacy_pending','building','ready','failed')) "
            "OR (state = 'failed' AND provenance_state IN ('none','failed')) "
            "OR (state = 'processing' AND provenance_state IN "
            "('none','building','failed')) "
            "OR (state = 'review' AND provenance_state = 'ready') "
            "OR (state IN ('rejected','superseded','obsolete') "
            "AND provenance_state = 'ready'))) OR "
            "(version_label <> 'Legacy 1' AND version_key <> 'legacy 1' "
            "AND provenance_state <> 'legacy_pending' "
            "AND parser_profile_version <> 'legacy/parser-v1' "
            "AND ocr_profile_version <> 'legacy/ocr-unknown-v1' "
            "AND chunking_profile_version <> 'legacy/chunking-v1' "
            "AND embedding_profile_version <> 'legacy/embedding-v1' "
            "AND index_profile_version <> 'legacy/index-v1')",
            name="ck_document_versions_exact_legacy_contract",
        ),
        CheckConstraint(
            "source_page_count IS NOT NULL OR "
            "((version_label <> 'Legacy 1' AND version_key <> 'legacy 1') "
            "AND state IN ('draft','processing','failed') "
            "AND provenance_state <> 'ready') OR "
            "(sequence = 1 AND version_label = 'Legacy 1' AND version_key = 'legacy 1' "
            "AND ((state = 'approved' AND provenance_state = 'legacy_pending') "
            "OR (state = 'failed' AND provenance_state IN ('none','failed')) "
            "OR (state = 'processing' AND provenance_state IN "
            "('none','building','failed'))))",
            name="ck_document_versions_page_count_or_exact_legacy",
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
        CheckConstraint(
            "(source_delete_requested_at IS NULL "
            "AND source_delete_requested_by IS NULL "
            "AND source_deleted_at IS NULL) OR "
            "(source_delete_requested_at IS NOT NULL "
            "AND source_delete_requested_by IS NOT NULL "
            "AND (source_deleted_at IS NULL "
            "OR source_deleted_at >= source_delete_requested_at))",
            name="ck_document_versions_source_deletion_markers",
        ),
        CheckConstraint(
            "NOT legacy_approval_backfilled OR "
            "(id = document_id AND sequence = 1 "
            "AND version_label = 'Legacy 1' AND version_key = 'legacy 1')",
            name="ck_document_versions_legacy_approval_backfill_scope",
        ),
        CheckConstraint(
            "NOT (id = document_id AND sequence = 1 "
            "AND version_label = 'Legacy 1' AND version_key = 'legacy 1' "
            "AND state = 'approved') OR "
            "(approved_by IS NOT NULL AND approved_at IS NOT NULL "
            "AND decision_at IS NOT NULL)",
            name="ck_document_versions_legacy_approval_evidence",
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
        ForeignKeyConstraint(
            ["org_id", "source_delete_requested_by"],
            ["users.org_id", "users.id"],
            name="fk_document_versions_org_source_delete_requester",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    document_id: Mapped[UUID] = mapped_column(index=True)
    sequence: Mapped[int]
    version_label: Mapped[str] = mapped_column(String(200))
    version_key: Mapped[str] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64))
    source_filename: Mapped[str | None] = mapped_column(String(500), default=None)
    source_mime: Mapped[str | None] = mapped_column(String(255), default=None)
    source_size_bytes: Mapped[int | None] = mapped_column(default=None)
    source_storage_key: Mapped[str | None] = mapped_column(String(1024), default=None)
    source_page_count: Mapped[int | None] = mapped_column(default=None)
    revision_date: Mapped[datetime | None] = mapped_column(default=None)
    parser_profile_version: Mapped[str] = mapped_column(String(100))
    ocr_profile_version: Mapped[str] = mapped_column(String(100))
    chunking_profile_version: Mapped[str] = mapped_column(String(100))
    embedding_profile_version: Mapped[str] = mapped_column(String(100))
    index_profile_version: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(default=DocumentVersionState.DRAFT.value, index=True)
    provenance_state: Mapped[str] = mapped_column(default=ProvenanceState.NONE.value, index=True)
    lifecycle_revision: Mapped[int] = mapped_column(default=1)
    superseded_by_id: Mapped[UUID | None] = mapped_column(default=None)
    effective_at: Mapped[datetime | None] = mapped_column(default=None)
    expires_at: Mapped[datetime | None] = mapped_column(default=None)
    created_by: Mapped[UUID]
    approved_by: Mapped[UUID | None] = mapped_column(default=None)
    approved_at: Mapped[datetime | None] = mapped_column(default=None)
    rejected_by: Mapped[UUID | None] = mapped_column(default=None)
    rejected_at: Mapped[datetime | None] = mapped_column(default=None)
    obsolete_by: Mapped[UUID | None] = mapped_column(default=None)
    obsolete_at: Mapped[datetime | None] = mapped_column(default=None)
    superseded_at: Mapped[datetime | None] = mapped_column(default=None)
    decision_at: Mapped[datetime | None] = mapped_column(default=None)
    processing_error_code: Mapped[str | None] = mapped_column(String(100), default=None)
    legacy_approval_backfilled: Mapped[bool] = mapped_column(
        default=False, server_default=text("false")
    )
    source_delete_requested_at: Mapped[datetime | None] = mapped_column(default=None)
    source_delete_requested_by: Mapped[UUID | None] = mapped_column(default=None)
    source_deleted_at: Mapped[datetime | None] = mapped_column(default=None)
    updated_at: Mapped[datetime] = mapped_column(default=naive_utc, onupdate=naive_utc)


class DocumentVersionDecisionRecord(UUIDPk, Base):
    __tablename__ = "document_version_decision_records"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "document_version_id",
            "lifecycle_revision",
            name="uq_document_version_decision_records_version_revision",
        ),
        CheckConstraint(
            "lifecycle_revision > 0",
            name="ck_document_version_decision_records_revision_positive",
        ),
        CheckConstraint(
            "decision IN ('approved','rejected','obsolete','superseded')",
            name="ck_document_version_decision_records_decision",
        ),
        CheckConstraint(
            "reason IS NULL OR char_length(btrim(reason)) BETWEEN 1 AND 500",
            name="ck_document_version_decision_records_reason_bounded",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "document_id", "document_version_id"],
            [
                "document_versions.org_id",
                "document_versions.workspace_id",
                "document_versions.document_id",
                "document_versions.id",
            ],
            name="fk_document_version_decision_records_exact_version",
        ),
        ForeignKeyConstraint(
            ["org_id", "actor_id"],
            ["users.org_id", "users.id"],
            name="fk_document_version_decision_records_org_actor",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    document_id: Mapped[UUID] = mapped_column(index=True)
    document_version_id: Mapped[UUID] = mapped_column(index=True)
    lifecycle_revision: Mapped[int]
    decision: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[UUID] = mapped_column(index=True)
    reason: Mapped[str | None] = mapped_column(String(500), default=None)


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
        CheckConstraint(
            "page_number > 0",
            name="ck_document_blocks_page_positive",
        ),
        CheckConstraint(
            "ocr_confidence IS NULL OR (ocr_confidence >= 0 AND ocr_confidence <= 1)",
            name="ck_document_blocks_ocr_confidence",
        ),
        CheckConstraint(
            "jsonb_typeof(section_path) = 'array' "
            "AND jsonb_array_length(section_path) BETWEEN 1 AND 8 "
            "AND pg_column_size(section_path) <= 4096 "
            "AND jsonb_array_length(jsonb_path_query_array(section_path, "
            '\'$[*] ? (@.type() == "string" && @ like_regex "^.{1,200}$" flag "s")\')) '
            "= jsonb_array_length(section_path)",
            name="ck_document_blocks_section_path",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_document_blocks_content_hash",
        ),
        CheckConstraint(
            "source_coordinates IS NULL OR "
            "(jsonb_typeof(source_coordinates) = 'object' "
            "AND pg_column_size(source_coordinates) <= 8192)",
            name="ck_document_blocks_source_coordinates",
        ),
        ForeignKeyConstraint(
            ["org_id", "document_version_id"],
            ["document_versions.org_id", "document_versions.id"],
            name="fk_document_blocks_org_version",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "document_version_id", "parent_block_id"],
            ["document_blocks.org_id", "document_blocks.document_version_id", "document_blocks.id"],
            name="fk_document_blocks_same_version_parent",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    document_version_id: Mapped[UUID] = mapped_column(index=True)
    parent_block_id: Mapped[UUID | None] = mapped_column(default=None)
    ordinal: Mapped[int]
    text: Mapped[str] = mapped_column(Text())
    page_number: Mapped[int]
    locator_kind: Mapped[str] = mapped_column(String(32))
    locator_label: Mapped[str] = mapped_column(String(200))
    block_type: Mapped[str] = mapped_column(String(50))
    section_path: Mapped[list[str]] = mapped_column(JSONB)
    source_coordinates: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True),
        default=None,
    )
    extraction_method: Mapped[str] = mapped_column(String(50))
    ocr_profile_version: Mapped[str] = mapped_column(String(100))
    ocr_confidence: Mapped[float | None] = mapped_column(default=None)
    content_hash: Mapped[str] = mapped_column(String(64))

    @validates("section_path")
    def normalize_section_path(self, _key: str, value: list[str]) -> list[str]:
        return list(validate_section_path(value))


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
        CheckConstraint(
            "page_start > 0",
            name="ck_document_chunks_page_start",
        ),
        CheckConstraint(
            "page_end > 0 AND page_end >= page_start",
            name="ck_document_chunks_page_range",
        ),
        CheckConstraint(
            "jsonb_typeof(section_path) = 'array' "
            "AND jsonb_array_length(section_path) BETWEEN 1 AND 8 "
            "AND pg_column_size(section_path) <= 4096 "
            "AND jsonb_array_length(jsonb_path_query_array(section_path, "
            '\'$[*] ? (@.type() == "string" && @ like_regex "^.{1,200}$" flag "s")\')) '
            "= jsonb_array_length(section_path)",
            name="ck_document_chunks_section_path",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_document_chunks_content_hash",
        ),
        CheckConstraint(
            "char_length(chunking_profile_version) BETWEEN 1 AND 100 "
            "AND char_length(embedding_profile_version) BETWEEN 1 AND 100",
            name="ck_document_chunks_profile_snapshot",
        ),
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
    page_start: Mapped[int]
    page_end: Mapped[int]
    section_path: Mapped[list[str]] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))
    chunking_profile_version: Mapped[str] = mapped_column(String(100))
    embedding_profile_version: Mapped[str] = mapped_column(String(100))

    @validates("section_path")
    def normalize_section_path(self, _key: str, value: list[str]) -> list[str]:
        return list(validate_section_path(value))


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
        CheckConstraint(
            "jsonb_typeof(section_path) = 'array' "
            "AND jsonb_array_length(section_path) BETWEEN 1 AND 8 "
            "AND pg_column_size(section_path) <= 4096 "
            "AND jsonb_array_length(jsonb_path_query_array(section_path, "
            '\'$[*] ? (@.type() == "string" && @ like_regex "^.{1,200}$" flag "s")\')) '
            "= jsonb_array_length(section_path)",
            name="ck_document_evidence_spans_section_path",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_document_evidence_spans_content_hash",
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
    section_path: Mapped[list[str]] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))
    ordinal: Mapped[int]
    token_count: Mapped[int]
    artifact_byte_start: Mapped[int]
    artifact_byte_end: Mapped[int]

    @validates("section_path")
    def normalize_section_path(self, _key: str, value: list[str]) -> list[str]:
        return list(validate_section_path(value))


class DocumentVersionProjection(UUIDPk, Base):
    __tablename__ = "document_version_projections"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "document_version_id", name="uq_document_version_projections_version"
        ),
        CheckConstraint(
            "applied_revision >= 1",
            name="ck_document_version_projections_applied_revision",
        ),
        CheckConstraint(
            "sync_state IN ('queued','leased','retry','applied','failed')",
            name="ck_document_version_projections_sync_state",
        ),
        CheckConstraint(
            "sync_attempts BETWEEN 0 AND 1000000",
            name="ck_document_version_projections_sync_attempts",
        ),
        CheckConstraint(
            "(sync_lease_owner IS NULL AND sync_lease_token IS NULL "
            "AND sync_lease_expires_at IS NULL) OR "
            "(sync_lease_owner IS NOT NULL AND sync_lease_token IS NOT NULL "
            "AND sync_lease_expires_at IS NOT NULL)",
            name="ck_document_version_projections_sync_lease",
        ),
        CheckConstraint(
            "vector_applied_revision IS NULL OR vector_applied_revision >= 1",
            name="ck_document_version_projections_vector_revision",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "document_version_id"],
            [
                "document_versions.org_id",
                "document_versions.workspace_id",
                "document_versions.id",
            ],
            name="fk_document_version_projections_org_workspace_version",
            ondelete="CASCADE",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    document_version_id: Mapped[UUID] = mapped_column(index=True)
    is_current_eligible: Mapped[bool] = mapped_column(default=False, index=True)
    applied_revision: Mapped[int]
    applied_at: Mapped[datetime] = mapped_column(default=naive_utc)
    sync_state: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    sync_attempts: Mapped[int] = mapped_column(default=0)
    sync_available_at: Mapped[datetime] = mapped_column(default=naive_utc, index=True)
    sync_lease_owner: Mapped[str | None] = mapped_column(String(200), default=None)
    sync_lease_token: Mapped[UUID | None] = mapped_column(default=None, index=True)
    sync_lease_expires_at: Mapped[datetime | None] = mapped_column(default=None)
    sync_error_code: Mapped[str | None] = mapped_column(String(100), default=None)
    vector_applied_generation_id: Mapped[UUID | None] = mapped_column(default=None)
    vector_applied_revision: Mapped[int | None] = mapped_column(default=None)
    vector_applied_at: Mapped[datetime | None] = mapped_column(default=None)


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
            "request_digest ~ '^[0-9a-f]{64}$'",
            name="ck_document_authority_readiness_request_digest",
        ),
        CheckConstraint("schema_version > 0", name="ck_document_authority_readiness_schema"),
        CheckConstraint(
            "char_length(physical_collection) BETWEEN 1 AND 200 "
            "AND char_length(collection_alias) BETWEEN 1 AND 200",
            name="ck_document_authority_readiness_collection_names",
        ),
        CheckConstraint(
            "grounding_policy_version > 0",
            name="ck_document_authority_readiness_policy_snapshot",
        ),
        CheckConstraint(
            "payload_index_digest IS NULL OR payload_index_digest ~ '^[0-9a-f]{64}$'",
            name="ck_document_authority_readiness_payload_digest",
        ),
        CheckConstraint(
            "provenance_digest IS NULL OR provenance_digest ~ '^[0-9a-f]{64}$'",
            name="ck_document_authority_readiness_provenance_digest",
        ),
        CheckConstraint(
            "lifecycle_revision_digest IS NULL OR lifecycle_revision_digest ~ '^[0-9a-f]{64}$'",
            name="ck_document_authority_readiness_lifecycle_digest",
        ),
        CheckConstraint(
            "calibration_hash ~ '^[0-9a-f]{64}$'",
            name="ck_document_authority_readiness_calibration_hash",
        ),
        CheckConstraint(
            "readiness_digest IS NULL OR readiness_digest ~ '^[0-9a-f]{64}$'",
            name="ck_document_authority_readiness_digest",
        ),
        CheckConstraint(
            "signature IS NULL OR signature ~ '^[0-9a-f]{64}$'",
            name="ck_document_authority_readiness_signature",
        ),
        CheckConstraint(
            "cardinality(blocker_codes) <= 32",
            name="ck_document_authority_readiness_blocker_count",
        ),
        CheckConstraint(
            "current_version_count >= 0 AND ready_version_count >= 0 "
            "AND ready_version_count <= current_version_count "
            "AND projected_version_count >= 0 "
            "AND projected_version_count <= current_version_count AND point_count >= 0",
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
            [
                "org_id",
                "workspace_id",
                "grounding_policy_id",
                "grounding_policy_version",
                "verifier_model_id",
                "calibration_hash",
                "provider_preset_version",
                "binding_revision",
                "credential_fingerprint",
            ],
            [
                "grounding_policies.org_id",
                "grounding_policies.workspace_id",
                "grounding_policies.id",
                "grounding_policies.policy_version",
                "grounding_policies.verifier_model_id",
                "grounding_policies.calibration_dataset_hash",
                "grounding_policies.provider_preset_version",
                "grounding_policies.binding_revision",
                "grounding_policies.credential_fingerprint",
            ],
            name="fk_document_authority_readiness_policy_snapshot",
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
    grounding_policy_id: Mapped[UUID]
    grounding_policy_version: Mapped[int]
    calibration_hash: Mapped[str] = mapped_column(String(64))
    verifier_model_id: Mapped[UUID] = mapped_column(ForeignKey("models.id"))
    provider_preset_version: Mapped[str] = mapped_column(String(100))
    binding_revision: Mapped[str] = mapped_column(String(100))
    credential_fingerprint: Mapped[str] = mapped_column(String(128))
    readiness_digest: Mapped[str | None] = mapped_column(String(64), default=None)
    signature: Mapped[str | None] = mapped_column(String(128), default=None)
    blocker_codes: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list)
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

    document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), default=None, index=True
    )
    org_id: Mapped[UUID | None] = mapped_column(default=None, index=True)
    document_version_id: Mapped[UUID | None] = mapped_column(default=None, index=True)
    stage: Mapped[str]
    progress: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[str | None] = mapped_column(default=None)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class DocumentEnrichmentJob(UUIDPk, Base):
    """Content-free durable work bound to one model and vector generation."""

    __tablename__ = "document_enrichment_jobs"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "embedding_deployment_id",
            "model_id",
            "model_probe_revision",
            "prompt_contract_version",
            "evidence_start_ordinal",
            name="uq_document_enrichment_jobs_generation",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "document_version_id"],
            [
                "document_versions.org_id",
                "document_versions.workspace_id",
                "document_versions.id",
            ],
            name="fk_document_enrichment_jobs_scope_version",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "source IN ('approval','backfill','reindex')",
            name="ck_document_enrichment_jobs_source",
        ),
        CheckConstraint(
            "status IN ('queued','running','completed','failed','skipped')",
            name="ck_document_enrichment_jobs_status",
        ),
        CheckConstraint(
            "attempts BETWEEN 0 AND 8",
            name="ck_document_enrichment_jobs_attempts",
        ),
        CheckConstraint(
            "model_probe_revision > 0",
            name="ck_document_enrichment_jobs_model_revision",
        ),
        CheckConstraint(
            "evidence_start_ordinal >= 0 "
            "AND evidence_end_ordinal > evidence_start_ordinal "
            "AND evidence_end_ordinal - evidence_start_ordinal <= 16",
            name="ck_document_enrichment_jobs_batch",
        ),
        CheckConstraint(
            "char_length(prompt_contract_version) BETWEEN 1 AND 100",
            name="ck_document_enrichment_jobs_prompt_version",
        ),
        CheckConstraint(
            "(status = 'running' AND lease_owner IS NOT NULL "
            "AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'running' AND lease_owner IS NULL "
            "AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_document_enrichment_jobs_lease",
        ),
        CheckConstraint(
            "total_evidence = evidence_end_ordinal - evidence_start_ordinal "
            "AND generated_evidence >= 0 "
            "AND invalid_evidence >= 0 AND prompt_tokens >= 0 "
            "AND completion_tokens >= 0 "
            "AND generated_evidence + invalid_evidence <= total_evidence "
            "AND (status <> 'completed' OR "
            "generated_evidence + invalid_evidence = total_evidence)",
            name="ck_document_enrichment_jobs_results",
        ),
        CheckConstraint(
            "error_code IS NULL OR char_length(error_code) BETWEEN 1 AND 64",
            name="ck_document_enrichment_jobs_error",
        ),
        Index(
            "ix_document_enrichment_jobs_claim",
            "status",
            "lease_expires_at",
            "created_at",
            "id",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    document_version_id: Mapped[UUID] = mapped_column(index=True)
    embedding_deployment_id: Mapped[UUID] = mapped_column(
        ForeignKey("embedding_deployments.id", ondelete="RESTRICT"),
        index=True,
    )
    model_id: Mapped[UUID] = mapped_column(
        ForeignKey("models.id", ondelete="RESTRICT"),
        index=True,
    )
    model_probe_revision: Mapped[int]
    prompt_contract_version: Mapped[str] = mapped_column(String(100))
    evidence_start_ordinal: Mapped[int]
    evidence_end_ordinal: Mapped[int]
    source: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(
        String(16), default="queued", server_default="queued", index=True
    )
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    lease_owner: Mapped[str | None] = mapped_column(String(200), default=None)
    lease_token: Mapped[UUID | None] = mapped_column(default=None, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    total_evidence: Mapped[int]
    generated_evidence: Mapped[int] = mapped_column(default=0, server_default="0")
    invalid_evidence: Mapped[int] = mapped_column(default=0, server_default="0")
    prompt_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    requested_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        default=None,
    )
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
        CheckConstraint(
            "pipeline_kind IN ('ingestion','rebuild','reindex')",
            name="ck_ingest_stage_attempts_pipeline_kind",
        ),
        CheckConstraint(
            "(pipeline_kind = 'reindex' "
            "AND embedding_deployment_id IS NOT NULL "
            "AND embedding_profile_version IS NOT NULL) OR "
            "(pipeline_kind <> 'reindex' "
            "AND embedding_deployment_id IS NULL "
            "AND embedding_profile_version IS NULL)",
            name="ck_ingest_stage_attempts_reindex_identity",
        ),
        CheckConstraint(
            "stage IN ('parse','chunk','embed','authority_upsert')",
            name="ck_ingest_stage_attempts_stage",
        ),
        CheckConstraint(
            "state IN ('queued','running','succeeded','failed','cancelled')",
            name="ck_ingest_stage_attempts_state",
        ),
        CheckConstraint(
            "attempts BETWEEN 0 AND 8",
            name="ck_ingest_stage_attempts_attempts_bounded",
        ),
        CheckConstraint(
            "(state = 'running' AND lease_owner IS NOT NULL "
            "AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(state <> 'running' AND lease_owner IS NULL "
            "AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_ingest_stage_attempts_lease_fenced",
        ),
        CheckConstraint(
            "output_digest IS NULL OR output_digest ~ '^[0-9a-f]{64}$'",
            name="ck_ingest_stage_attempts_output_digest",
        ),
        CheckConstraint(
            "error_code IS NULL OR char_length(error_code) BETWEEN 1 AND 100",
            name="ck_ingest_stage_attempts_error_code_bounded",
        ),
        Index(
            "ix_ingest_stage_attempts_claimable",
            "available_at",
            "created_at",
            postgresql_where=text("state IN ('queued','running')"),
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "document_version_id"],
            [
                "document_versions.org_id",
                "document_versions.workspace_id",
                "document_versions.id",
            ],
            name="fk_ingest_stage_attempts_org_workspace_version",
            ondelete="CASCADE",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    document_version_id: Mapped[UUID] = mapped_column(index=True)
    embedding_deployment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("embedding_deployments.id", ondelete="CASCADE"),
        default=None,
        index=True,
    )
    embedding_profile_version: Mapped[str | None] = mapped_column(
        String(100),
        default=None,
    )
    pipeline_kind: Mapped[str] = mapped_column(String(32))
    stage: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(default="queued", index=True)
    checkpoint: Mapped[str] = mapped_column(String(128))
    lease_owner: Mapped[str | None] = mapped_column(String(200), default=None)
    lease_token: Mapped[UUID | None] = mapped_column(default=None, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(default=None)
    attempts: Mapped[int] = mapped_column(default=0)
    error_code: Mapped[str | None] = mapped_column(String(100), default=None)
    available_at: Mapped[datetime] = mapped_column(
        default=naive_utc,
        server_default=text("timezone('UTC', now())"),
    )
    output_digest: Mapped[str | None] = mapped_column(String(64), default=None)
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
    cursor_document_version_id: Mapped[UUID | None] = mapped_column(default=None)
    pass_number: Mapped[int] = mapped_column(default=0)
    scanned_count: Mapped[int] = mapped_column(default=0)
    emitted_count: Mapped[int] = mapped_column(default=0)
    skipped_count: Mapped[int] = mapped_column(default=0)
    pass_started_at: Mapped[datetime | None] = mapped_column(default=None)
    completed_at: Mapped[datetime | None] = mapped_column(default=None)
    updated_at: Mapped[datetime] = mapped_column(default=naive_utc, onupdate=naive_utc)
