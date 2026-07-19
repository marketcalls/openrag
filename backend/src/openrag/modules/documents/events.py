"""Strict, content-free document lifecycle event contracts."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from openrag.modules.documents.lifecycle import DocumentVersionState


class DocumentVersionEventV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    org_id: UUID
    workspace_id: UUID
    document_id: UUID
    document_version_id: UUID
    previous_state: DocumentVersionState
    new_state: DocumentVersionState
    lifecycle_revision: int = Field(gt=0)
    actor_id: UUID
    occurred_at: AwareDatetime

    @property
    def dedupe_key(self) -> str:
        return (
            f"document-version:{self.document_version_id}:"
            f"{self.lifecycle_revision}"
        )
