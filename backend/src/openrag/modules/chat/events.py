"""Typed Server-Sent Event frames consumed by the OpenRAG frontend."""

import json
from dataclasses import asdict, dataclass
from typing import Literal

from openrag.modules.artifacts.schemas import AnalyticsResponseV1


@dataclass(frozen=True)
class SSEEvent:
    event: str
    data: dict[str, object]

    def encode(self) -> str:
        payload = json.dumps(self.data, separators=(",", ":"))
        return f"event: {self.event}\ndata: {payload}\n\n"


@dataclass(frozen=True)
class SourceRef:
    marker: int
    document_id: str
    filename: str
    page: int
    chunk_index: int
    score: float
    snippet: str
    document_version_id: str | None = None
    evidence_span_id: str | None = None
    version_label: str | None = None
    section_label: str | None = None
    section_path: list[str] | None = None
    locator_kind: str | None = None
    locator_label: str | None = None
    content_hash: str | None = None
    dense_score: float | None = None
    sparse_score: float | None = None
    fused_score: float | None = None
    rerank_score: float | None = None


@dataclass(frozen=True)
class CitationRef:
    marker: int
    document_id: str
    chunk_ref: str
    page: int
    score: float
    document_version_id: str | None = None
    evidence_span_id: str | None = None
    document_name: str | None = None
    version_label: str | None = None
    section_label: str | None = None
    section_path: list[str] | None = None
    locator_kind: str | None = None
    locator_label: str | None = None
    content_hash: str | None = None
    dense_score: float | None = None
    sparse_score: float | None = None
    fused_score: float | None = None
    rerank_score: float | None = None


def retrieval_started_event() -> SSEEvent:
    return SSEEvent("retrieval_started", {})


def agent_started_event(reason_code: str) -> SSEEvent:
    return SSEEvent("agent_started", {"reason_code": reason_code})


def tool_progress_event(
    *,
    iteration: int,
    stage: Literal["started", "completed", "failed"],
    tool: Literal["search", "search_by_metadata", "get_document"],
) -> SSEEvent:
    return SSEEvent(
        "tool_progress",
        {"iteration": iteration, "stage": stage, "tool": tool},
    )


def agent_completed_event(finish_reason: str) -> SSEEvent:
    return SSEEvent("agent_completed", {"finish_reason": finish_reason})


def route_selected_event(route: str, reason_code: str) -> SSEEvent:
    return SSEEvent(
        "route_selected",
        {"route": route, "reason_code": reason_code},
    )


def sources_event(sources: list[SourceRef]) -> SSEEvent:
    return SSEEvent(
        "sources",
        {"sources": [asdict(source) for source in sources]},
    )


def token_event(delta: str) -> SSEEvent:
    return SSEEvent("token", {"delta": delta})


def citations_event(citations: list[CitationRef]) -> SSEEvent:
    return SSEEvent(
        "citations",
        {"citations": [asdict(citation) for citation in citations]},
    )


def analytics_artifact_event(artifact: AnalyticsResponseV1) -> SSEEvent:
    """Carry one already-validated supplemental presentation artifact."""

    return SSEEvent(
        "analytics_artifact",
        {"artifact": artifact.model_dump(mode="json")},
    )


def done_event(
    *,
    message_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    no_answer: bool,
) -> SSEEvent:
    return SSEEvent(
        "done",
        {
            "message_id": message_id,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "no_answer": no_answer,
        },
    )


def error_event(detail: str) -> SSEEvent:
    return SSEEvent("error", {"detail": detail})
