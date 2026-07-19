from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MessageSend(BaseModel):
    content: str = Field(min_length=1, max_length=32000)
    parent_message_id: UUID | None = None
    model_id: UUID | None = None


class RegenerateRequest(BaseModel):
    model_id: UUID | None = None


class ChatCreate(BaseModel):
    workspace_id: UUID
    title: str | None = Field(default=None, min_length=1, max_length=200)


class ChatOut(BaseModel):
    id: UUID
    workspace_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ChatPatch(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class CitationOut(BaseModel):
    marker: int
    document_id: UUID
    chunk_ref: str
    page: int
    score: float
    document_name: str | None
    version_label: str | None
    section_label: str | None
    section_path: list[str] | None
    locator_kind: str | None
    locator_label: str | None
    verification_state: str | None

    model_config = ConfigDict(from_attributes=True)


class MessageNode(BaseModel):
    id: UUID
    parent_message_id: UUID | None
    sibling_index: int
    role: str
    content: str
    model_id: UUID | None
    prompt_tokens: int | None
    completion_tokens: int | None
    created_at: datetime
    citations: list[CitationOut]
    children: list["MessageNode"]


MessageNode.model_rebuild()


class ChatTreeOut(BaseModel):
    id: UUID
    workspace_id: UUID
    title: str
    messages: list[MessageNode]
