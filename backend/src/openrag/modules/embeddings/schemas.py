"""Strict public contracts for immutable embedding configurations."""

import hashlib
import json
import re
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EmbeddingProviderKind = Literal["litellm", "tei", "hash"]
EmbeddingDeploymentStatus = Literal[
    "building",
    "ready",
    "active",
    "failed",
    "retired",
]
_WHITESPACE = re.compile(r"\s+")


class EmbeddingProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=120)
    provider_kind: EmbeddingProviderKind
    model_name: str = Field(min_length=1, max_length=200)
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: str | None = Field(default=None, min_length=1, max_length=8192, repr=False)
    dimension: int = Field(ge=1, le=32768)
    max_input_tokens: int = Field(default=8192, ge=1, le=2_000_000)
    batch_size: int = Field(default=32, ge=1, le=1024)

    @field_validator("name", "model_name")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = _WHITESPACE.sub(" ", value).strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("base_url must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_provider_credentials(self) -> Self:
        if self.provider_kind == "litellm":
            if self.model_name.startswith("ollama/"):
                if self.base_url is None:
                    raise ValueError("base_url is required for ollama embeddings")
            elif self.api_key is None:
                raise ValueError("api_key is required for hosted embeddings")
        elif self.api_key is not None or self.base_url is not None:
            raise ValueError(
                "provider credentials are only accepted for LiteLLM profiles"
            )
        return self


class EmbeddingProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    api_key: str | None = Field(default=None, min_length=1, max_length=8192, repr=False)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _WHITESPACE.sub(" ", value).strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized


class EmbeddingProfileOut(BaseModel):
    id: UUID
    name: str
    provider_kind: EmbeddingProviderKind
    model_name: str
    base_url: str | None
    dimension: int
    max_input_tokens: int
    batch_size: int
    config_digest: str
    enabled: bool
    key_fingerprint: str | None

    model_config = ConfigDict(from_attributes=True, frozen=True)


class EmbeddingDeploymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: UUID


class EmbeddingDeploymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    profile_id: UUID
    generation_id: UUID
    status: EmbeddingDeploymentStatus
    total_versions: int = Field(ge=0)
    completed_versions: int = Field(ge=0)
    failed_versions: int = Field(ge=0)
    scan_complete: bool
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None
    failure_code: str | None

    @model_validator(mode="after")
    def validate_progress(self) -> Self:
        if self.completed_versions + self.failed_versions > self.total_versions:
            raise ValueError("deployment progress exceeds total versions")
        return self


def embedding_config_digest(profile: EmbeddingProfileCreate) -> str:
    encoded = json.dumps(
        {
            "schema_version": 1,
            "provider_kind": profile.provider_kind,
            "model_name": profile.model_name,
            "base_url": profile.base_url,
            "dimension": profile.dimension,
            "max_input_tokens": profile.max_input_tokens,
            "batch_size": profile.batch_size,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()
