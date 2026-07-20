from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openrag.modules.models.reasoning import ReasoningEffort

ProviderKind = Literal["openai", "ollama", "openai_compatible"]
SyncStatus = Literal["synced", "error", "pending"]


class ModelCreate(BaseModel):
    litellm_model_name: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    provider_kind: ProviderKind
    base_url: str | None = None
    api_key: str | None = None
    supports_reasoning: bool = False
    default_reasoning_effort: ReasoningEffort = "off"

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
        if self.default_reasoning_effort != "off" and not self.supports_reasoning:
            raise ValueError(
                "default_reasoning_effort requires supports_reasoning"
            )
        return self


class ModelPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = None
    enabled: bool | None = None
    api_key: str | None = None
    supports_reasoning: bool | None = None
    default_reasoning_effort: ReasoningEffort | None = None

    @model_validator(mode="after")
    def _explicit_reasoning_capability_is_consistent(self) -> "ModelPatch":
        if (
            self.supports_reasoning is False
            and self.default_reasoning_effort not in (None, "off")
        ):
            raise ValueError(
                "default_reasoning_effort requires supports_reasoning"
            )
        return self


class ModelOut(BaseModel):
    id: UUID
    litellm_model_name: str
    display_name: str
    provider_kind: ProviderKind
    base_url: str | None
    enabled: bool
    key_fingerprint: str | None
    sync_status: SyncStatus
    supports_reasoning: bool
    default_reasoning_effort: ReasoningEffort


class ModelPublic(BaseModel):
    id: UUID
    display_name: str
    supports_reasoning: bool
    default_reasoning_effort: ReasoningEffort

    model_config = ConfigDict(from_attributes=True)
