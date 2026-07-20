from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openrag.modules.models.reasoning import ReasoningEffort

ProviderKind = Literal["openai", "ollama", "openai_compatible"]


class ModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    litellm_model_name: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    provider_kind: ProviderKind
    base_url: str | None = None
    api_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=8192,
        repr=False,
    )
    supports_chat_completion: bool = True
    supports_structured_json: bool = False
    supports_verifier: bool = False
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
        if self.supports_structured_json and not self.supports_chat_completion:
            raise ValueError("structured JSON capability requires chat capability")
        if self.supports_verifier and not self.supports_structured_json:
            raise ValueError("verifier capability requires structured JSON capability")
        return self


class ModelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = None
    enabled: bool | None = None
    api_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=8192,
        repr=False,
    )
    supports_chat_completion: bool | None = None
    supports_structured_json: bool | None = None
    supports_verifier: bool | None = None
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
        if (
            self.supports_chat_completion is False
            and self.supports_structured_json is True
        ):
            raise ValueError("structured JSON capability requires chat capability")
        if self.supports_structured_json is False and self.supports_verifier is True:
            raise ValueError("verifier capability requires structured JSON capability")
        if self.supports_chat_completion is False and self.supports_verifier is True:
            raise ValueError("verifier capability requires chat capability")
        return self


class ModelOut(BaseModel):
    id: UUID
    litellm_model_name: str
    display_name: str
    provider_kind: ProviderKind
    base_url: str | None
    enabled: bool
    key_fingerprint: str | None
    supports_chat_completion: bool
    supports_structured_json: bool
    supports_verifier: bool
    supports_reasoning: bool
    default_reasoning_effort: ReasoningEffort


class ModelPublic(BaseModel):
    id: UUID
    display_name: str
    supports_reasoning: bool
    default_reasoning_effort: ReasoningEffort

    model_config = ConfigDict(from_attributes=True)
