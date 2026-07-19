"""Safe public document and version API contracts."""

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openrag.modules.documents.models import Document, DocumentVersion

_SAFE_PROCESSING_ERROR_CODES = frozenset(
    {
        "dispatch_failed",
        "ingest_failed",
        "parser_failed",
        "safe_parser_failure",
    }
)


def _safe_error_code(value: str | None) -> str | None:
    if value is None:
        return None
    if value in _SAFE_PROCESSING_ERROR_CODES:
        return value
    return "processing_failed"


class DocumentOut(BaseModel):
    id: UUID
    filename: str | None
    mime: str | None
    size_bytes: int | None
    status: str | None
    page_count: int | None
    error_code: str | None
    created_at: datetime

    model_config = ConfigDict(frozen=True)

    @classmethod
    def from_document(cls, document: Document) -> Self:
        return cls(
            id=document.id,
            filename=document.filename,
            mime=document.mime,
            size_bytes=document.size_bytes,
            status=document.status,
            page_count=document.page_count,
            error_code=_safe_error_code(document.error),
            created_at=document.created_at,
        )


class DocumentDetailOut(BaseModel):
    id: UUID
    workspace_id: UUID
    name: str
    department: str | None
    document_type: str | None
    external_identifier: str | None
    filename: str | None
    mime: str | None
    size_bytes: int | None
    status: str | None
    page_count: int | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(frozen=True)

    @classmethod
    def from_document(cls, document: Document) -> Self:
        return cls(
            id=document.id,
            workspace_id=document.workspace_id,
            name=document.name,
            department=document.department,
            document_type=document.document_type,
            external_identifier=document.external_identifier,
            filename=document.filename,
            mime=document.mime,
            size_bytes=document.size_bytes,
            status=document.status,
            page_count=document.page_count,
            error_code=_safe_error_code(document.error),
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class DocumentPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    department: str | None = Field(default=None, min_length=1, max_length=120)
    document_type: str | None = Field(default=None, min_length=1, max_length=120)
    external_identifier: str | None = Field(default=None, min_length=1, max_length=255)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("name", "department", "document_type", "external_identifier")
    @classmethod
    def normalize_metadata(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("metadata cannot be blank")
        return normalized


class DocumentVersionDecision(BaseModel):
    reason: str | None = Field(default=None, min_length=1, max_length=500)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("reason cannot be blank")
        return normalized


class DocumentVersionOut(BaseModel):
    id: UUID
    document_id: UUID
    sequence: int
    version_label: str
    state: str
    provenance_state: str
    page_count: int | None
    error_code: str | None
    revision_date: datetime | None
    effective_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    lifecycle_revision: int

    model_config = ConfigDict(frozen=True)

    @classmethod
    def from_version(cls, version: DocumentVersion) -> Self:
        return cls(
            id=version.id,
            document_id=version.document_id,
            sequence=version.sequence,
            version_label=version.version_label,
            state=version.state,
            provenance_state=version.provenance_state,
            page_count=version.source_page_count,
            error_code=_safe_error_code(version.processing_error_code),
            revision_date=version.revision_date,
            effective_at=version.effective_at,
            expires_at=version.expires_at,
            created_at=version.created_at,
            updated_at=version.updated_at,
            lifecycle_revision=version.lifecycle_revision,
        )
