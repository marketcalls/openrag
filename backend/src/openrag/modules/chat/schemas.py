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

    model_config = ConfigDict(from_attributes=True)
