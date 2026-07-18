from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

ProviderKind = Literal["openai", "ollama", "openai_compatible"]
SyncStatus = Literal["synced", "error", "pending"]


class ModelCreate(BaseModel):
    litellm_model_name: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    provider_kind: ProviderKind
    base_url: str | None = None
    api_key: str | None = None

    @model_validator(mode="after")
    def _base_url_required_for_self_hosted(self) -> "ModelCreate":
        if (
            self.provider_kind in ("ollama", "openai_compatible")
            and not self.base_url
        ):
            raise ValueError(
                "base_url is required for ollama and "
                "openai_compatible providers"
            )
        return self


class ModelPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = None
    enabled: bool | None = None
    api_key: str | None = None


class ModelOut(BaseModel):
    id: UUID
    litellm_model_name: str
    display_name: str
    provider_kind: ProviderKind
    base_url: str | None
    enabled: bool
    key_fingerprint: str | None
    sync_status: SyncStatus


class ModelPublic(BaseModel):
    id: UUID
    display_name: str

    model_config = ConfigDict(from_attributes=True)

