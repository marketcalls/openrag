from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OrgQuotaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monthly_tokens: int = Field(ge=0)
    default_user_monthly_tokens: int | None = Field(default=None, ge=0)
    reset_day: int = Field(default=1, ge=1, le=31)


class OrgQuotaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    org_id: UUID
    monthly_tokens: int
    default_user_monthly_tokens: int | None
    reset_day: int


class UserQuotaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monthly_tokens: int | None = Field(default=None, ge=0)


class UserQuotaOut(BaseModel):
    user_id: UUID
    monthly_tokens: int | None
    used_tokens: int
    allocated_tokens: int | None
    resets_at: datetime


class UsageMeterOut(BaseModel):
    used_tokens: int
    allocated_tokens: int | None
    org_used_tokens: int
    org_allocated_tokens: int | None
    resets_at: datetime
    warning: bool
    blocked: bool
