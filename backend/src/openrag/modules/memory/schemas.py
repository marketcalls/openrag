"""Strict public contracts for user-controlled memory."""

import json
import re
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MemoryType = Literal["semantic", "episodic"]
MemoryScope = Literal["user_workspace"]
MemoryStatus = Literal[
    "candidate",
    "active",
    "conflicted",
    "superseded",
    "retracted",
    "expired",
    "quarantined",
]
Sensitivity = Literal["public", "internal", "confidential", "restricted"]

_KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,119}$")


def _bounded_object(value: dict[str, object] | None) -> dict[str, object] | None:
    if value is None:
        return None
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError("structured value must be JSON serializable") from exc
    if len(encoded) > 8192:
        raise ValueError("structured value must not exceed 8192 bytes")
    return value


class MemoryCreate(BaseModel):
    client_request_id: UUID
    canonical_key: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=4000)
    structured_value: dict[str, object] | None = None
    memory_type: MemoryType
    scope: MemoryScope
    confidence: float = Field(default=1.0, ge=0, le=1)
    importance: float = Field(default=0.5, ge=0, le=1)
    sensitivity: Sensitivity = "internal"
    expires_at: datetime | None = None

    @field_validator("canonical_key", mode="before")
    @classmethod
    def normalize_key(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip().lower()
        if not isinstance(value, str) or _KEY_RE.fullmatch(value) is None:
            raise ValueError("canonical key must use lowercase letters, numbers, '.', '-', '_'")
        return value

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, value: object) -> object:
        if isinstance(value, str):
            value = " ".join(value.split())
        if not isinstance(value, str) or not value:
            raise ValueError("memory content must not be blank")
        return value

    @field_validator("structured_value")
    @classmethod
    def validate_structured_value(
        cls,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return _bounded_object(value)


class MemoryPatch(BaseModel):
    client_request_id: UUID
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    structured_value: dict[str, object] | None = None
    importance: float | None = Field(default=None, ge=0, le=1)
    sensitivity: Sensitivity | None = None
    expires_at: datetime | None = None

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, value: object) -> object:
        if isinstance(value, str):
            value = " ".join(value.split())
            if not value:
                raise ValueError("memory content must not be blank")
        return value

    @field_validator("structured_value")
    @classmethod
    def validate_structured_value(
        cls,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return _bounded_object(value)

    @model_validator(mode="after")
    def require_change(self) -> Self:
        fields = self.model_fields_set - {"client_request_id"}
        if not fields:
            raise ValueError("at least one memory change is required")
        return self


class MemoryForget(BaseModel):
    client_request_id: UUID


class MemoryPreferencePatch(BaseModel):
    extraction_enabled: bool | None = None
    semantic_enabled: bool | None = None
    episodic_enabled: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one preference change is required")
        return self


class MemoryProvenanceOut(BaseModel):
    source_kind: str
    source_event_id: UUID
    source_message_id: UUID | None
    source_hash: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoryOut(BaseModel):
    id: UUID
    workspace_id: UUID
    canonical_key: str
    content: str
    structured_value: dict[str, object] | None
    memory_type: str
    scope: str
    status: str
    confidence: float
    importance: float
    sensitivity: str
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    provenance: list[MemoryProvenanceOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class MemoryPageOut(BaseModel):
    items: list[MemoryOut]
    next_cursor: str | None


class MemoryPreferenceOut(BaseModel):
    workspace_id: UUID
    extraction_enabled: bool
    semantic_enabled: bool
    episodic_enabled: bool
    procedural_enabled: bool
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoryExportOut(BaseModel):
    exported_at: datetime
    items: list[MemoryOut]
    truncated: bool
