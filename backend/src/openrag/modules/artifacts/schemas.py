"""Closed contracts for safe, source-grounded analytical presentation."""

import json
import re
import unicodedata
from collections.abc import Iterable
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

ANALYTICS_SCHEMA_VERSION = "analytics.v1"
MAX_ANALYTICS_ARTIFACT_BYTES = 49_152
_UNSAFE_SCHEME_RE = re.compile(r"(?:https?|javascript|data):", re.IGNORECASE)
_HTML_TAG_RE = re.compile(
    r"<\s*/?\s*(?:script|iframe|svg|style|object|embed|link|meta)\b",
    re.IGNORECASE,
)
_COLUMN_KEY_RE = r"^[A-Za-z][A-Za-z0-9_]{0,39}$"

AnalyticsScalar = StrictStr | StrictInt | StrictFloat | None
AnalyticsNumber = StrictInt | StrictFloat | None


def _iter_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _iter_strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _iter_strings(item)


def _unsafe_text(value: str) -> bool:
    if _UNSAFE_SCHEME_RE.search(value) or _HTML_TAG_RE.search(value):
        return True
    return any(
        unicodedata.category(character) == "Cc" and character not in "\n\r\t"
        for character in value
    )


class StrictArtifactModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    @model_validator(mode="after")
    def reject_unsafe_text(self) -> Self:
        if any(_unsafe_text(value) for value in _iter_strings(self.model_dump())):
            raise ValueError("analytics_artifact_text_unsafe")
        return self


class SourceBoundArtifactModel(StrictArtifactModel):
    source_markers: list[int] = Field(min_length=1, max_length=16)

    @field_validator("source_markers")
    @classmethod
    def validate_source_markers(cls, markers: list[int]) -> list[int]:
        if any(isinstance(marker, bool) or not 1 <= marker <= 999 for marker in markers):
            raise ValueError("analytics_artifact_marker_invalid")
        if len(markers) != len(set(markers)):
            raise ValueError("analytics_artifact_marker_duplicate")
        return markers


class AnalyticsKpiV1(SourceBoundArtifactModel):
    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=80)
    detail: str | None = Field(default=None, max_length=200)
    trend: Literal["up", "down", "flat", "none"] = "none"


class AnalyticsSeriesV1(StrictArtifactModel):
    name: str = Field(min_length=1, max_length=80)
    values: list[AnalyticsNumber] = Field(min_length=1, max_length=50)


class AnalyticsChartBlockV1(SourceBoundArtifactModel):
    kind: Literal["bar_chart", "line_chart"]
    title: str = Field(min_length=1, max_length=160)
    x_label: str = Field(min_length=1, max_length=80)
    y_label: str = Field(min_length=1, max_length=80)
    categories: list[str] = Field(min_length=1, max_length=50)
    series: list[AnalyticsSeriesV1] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_chart_shape(self) -> Self:
        if len(self.categories) != len(set(self.categories)):
            raise ValueError("analytics_artifact_category_duplicate")
        if len({series.name for series in self.series}) != len(self.series):
            raise ValueError("analytics_artifact_series_duplicate")
        if any(len(series.values) != len(self.categories) for series in self.series):
            raise ValueError("analytics_artifact_chart_shape_invalid")
        return self


class AnalyticsColumnV1(StrictArtifactModel):
    key: str = Field(min_length=1, max_length=40, pattern=_COLUMN_KEY_RE)
    label: str = Field(min_length=1, max_length=80)
    format: Literal["text", "number", "currency", "percent", "date"] = "text"


class AnalyticsTableBlockV1(SourceBoundArtifactModel):
    kind: Literal["table"]
    title: str = Field(min_length=1, max_length=160)
    columns: list[AnalyticsColumnV1] = Field(min_length=1, max_length=12)
    rows: list[dict[str, AnalyticsScalar]] = Field(max_length=200)

    @model_validator(mode="after")
    def validate_table_shape(self) -> Self:
        keys = [column.key for column in self.columns]
        if len(keys) != len(set(keys)):
            raise ValueError("analytics_artifact_column_duplicate")
        expected = set(keys)
        if any(set(row) != expected for row in self.rows):
            raise ValueError("analytics_artifact_table_shape_invalid")
        return self


class AnalyticsExplainerBlockV1(SourceBoundArtifactModel):
    kind: Literal["explainer"]
    title: str = Field(min_length=1, max_length=160)
    body_markdown: str = Field(min_length=1, max_length=8_000)


AnalyticsBlockV1 = Annotated[
    AnalyticsChartBlockV1 | AnalyticsTableBlockV1 | AnalyticsExplainerBlockV1,
    Field(discriminator="kind"),
]


class AnalyticsResponseV1(StrictArtifactModel):
    schema_version: Literal["analytics.v1"]
    title: str = Field(min_length=1, max_length=160)
    subtitle: str | None = Field(default=None, max_length=240)
    kpis: list[AnalyticsKpiV1] = Field(max_length=8)
    blocks: list[AnalyticsBlockV1] = Field(min_length=1, max_length=12)
    suggested_followups: list[str] = Field(max_length=5)

    @field_validator("suggested_followups")
    @classmethod
    def validate_followups(cls, values: list[str]) -> list[str]:
        if any(not 1 <= len(value) <= 240 for value in values):
            raise ValueError("analytics_artifact_followup_invalid")
        normalized = [" ".join(value.split()).casefold() for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("analytics_artifact_followup_duplicate")
        return values

    @model_validator(mode="after")
    def validate_serialized_size(self) -> Self:
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > MAX_ANALYTICS_ARTIFACT_BYTES:
            raise ValueError("analytics_artifact_too_large")
        return self


class MessageArtifactOut(StrictArtifactModel):
    """Content-addressed artifact returned with a historical message."""

    id: UUID
    message_id: UUID
    kind: Literal["analytics"]
    schema_version: Literal["analytics.v1"]
    artifact: AnalyticsResponseV1
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
