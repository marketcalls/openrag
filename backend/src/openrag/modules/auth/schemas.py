from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from openrag.modules.tenancy.schemas import RoleOut


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type, not a secret


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role_id: UUID


class InvitationOut(BaseModel):
    invite_token: str


class InvitationAccept(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str
    password: str = Field(min_length=8, max_length=1024)


class UserOut(BaseModel):
    id: UUID
    email: EmailStr
    active: bool
    is_platform_superadmin: bool
    roles: list[RoleOut]


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool | None = None
