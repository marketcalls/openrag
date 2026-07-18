from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    id: UUID
    filename: str
    mime: str
    size_bytes: int
    status: str
    page_count: int | None
    error: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
