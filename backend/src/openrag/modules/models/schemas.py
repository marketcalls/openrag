from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openrag.modules.models.reasoning import ReasoningEffort

ProviderKind = Literal["openai", "ollama", "openai_compatible"]
ModelProbeStatus = Literal["pending", "passed", "failed"]
ModelProbeRunStatus = Literal["queued", "running", "passed", "failed", "stale"]


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
    default_reasoning_effort: ReasoningEffort | None = None


class ModelOut(BaseModel):
    id: UUID
    litellm_model_name: str
    display_name: str
    provider_kind: ProviderKind
    base_url: str | None
    enabled: bool
    key_fingerprint: str | None
    supports_chat_completion: bool
    supports_streaming: bool
    supports_structured_json: bool
    supports_verifier: bool
    supports_tools: bool
    supports_vision: bool
    supports_reasoning: bool
    context_window: int | None
    default_reasoning_effort: ReasoningEffort
    probe_status: ModelProbeStatus
    probe_revision: int
    probe_latency_ms: int | None
    last_probe_error_code: str | None
    last_probed_at: datetime | None


class ModelProbeOut(BaseModel):
    id: UUID
    model_id: UUID
    revision: int
    status: ModelProbeRunStatus
    supports_chat_completion: bool
    supports_streaming: bool
    supports_structured_json: bool
    supports_tools: bool
    supports_vision: bool
    supports_reasoning: bool
    context_window: int | None
    latency_ms: int | None
    error_code: str | None
    requested_by: UUID | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class ModelPublic(BaseModel):
    id: UUID
    display_name: str
    supports_reasoning: bool
    default_reasoning_effort: ReasoningEffort

    model_config = ConfigDict(from_attributes=True)
