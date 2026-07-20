from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openrag.modules.tenancy.permissions import PermissionCode


class RoleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=500)
    permissions: set[PermissionCode]


class RolePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    permissions: set[PermissionCode] | None = None


class RoleOut(BaseModel):
    id: UUID
    key: str
    name: str
    description: str
    permissions: list[PermissionCode]
    is_system: bool
    is_assignable: bool


class PermissionCatalogOut(BaseModel):
    code: PermissionCode
    label: str
    group: str
    description: str


class RoleBindingReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_ids: list[UUID] = Field(max_length=16)

    @field_validator("role_ids")
    @classmethod
    def unique_role_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("role_ids must be unique")
        return value


class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=120)


class WorkspaceOut(BaseModel):
    id: UUID
    name: str
    embedding_model: str
    min_score: float
    default_model_id: UUID | None
    enrichment_enabled: bool

    model_config = {"from_attributes": True}


class MemberAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID


class WorkspaceMemberOut(BaseModel):
    user_id: UUID
    email: str


class WorkspacePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_model_id: UUID | None = None
    enrichment_enabled: bool | None = None

    @model_validator(mode="after")
    def exactly_one_setting(self) -> "WorkspacePatch":
        if len(self.model_fields_set) != 1:
            raise ValueError("exactly one workspace setting must be provided")
        return self
