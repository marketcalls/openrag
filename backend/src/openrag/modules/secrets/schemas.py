from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SecretWrite(BaseModel):
    value: str = Field(min_length=1, max_length=8192)


class SecretOut(BaseModel):
    name: str
    fingerprint: str
    last_used_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
