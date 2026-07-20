"""Provider-neutral policy and execution bounds for read-only agent tools."""

import asyncio
import html
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from openrag.modules.orchestration.routing import QueryRoute

AgentToolName = Literal["search", "search_by_metadata", "get_document"]
AgentFinishReason = Literal[
    "planner_finished",
    "iteration_limit",
    "duplicate_tool_call",
    "planner_timeout",
    "planner_failed",
    "tool_timeout",
    "tool_failed",
    "observation_budget_exhausted",
]
MetadataScalar = str | int | float | bool

_ALLOWED_TOOLS = frozenset({"search", "search_by_metadata", "get_document"})
_ALLOWED_METADATA_KEYS = frozenset(
    {
        "document_name",
        "department",
        "document_type",
        "version_label",
        "revision_date_from",
        "revision_date_to",
        "section",
    }
)
_MULTI_PART_TERMS = re.compile(
    r"\b(?:compare|explain|summarize|list|identify|calculate|show|why|how|what|which)\b",
    re.IGNORECASE,
)
_METADATA_TERMS = re.compile(
    r"\b(?:latest|approved|obsolete|superseded|version|revision|effective|"
    r"department|document type|section|page|sheet|slide|author|date range)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class EscalationContext:
    query: str
    route: QueryRoute
    weak_evidence: bool = False


@dataclass(frozen=True, slots=True)
class EscalationDecision:
    escalate: bool
    reason_code: str


def decide_escalation(context: EscalationContext) -> EscalationDecision:
    """Keep the common path single-pass and escalate only bounded hard cases."""

    query = " ".join(context.query.split())
    if context.route is QueryRoute.ANALYTICS:
        return EscalationDecision(True, "analytics_request")
    if context.route is not QueryRoute.RAG:
        return EscalationDecision(False, "single_pass")

    interrogatives = _MULTI_PART_TERMS.findall(query)
    if query.count("?") > 1 or len(interrogatives) >= 2:
        return EscalationDecision(True, "multi_part_query")
    if _METADATA_TERMS.search(query) is not None:
        return EscalationDecision(True, "metadata_sensitive")
    if context.weak_evidence:
        return EscalationDecision(True, "weak_evidence")
    return EscalationDecision(False, "single_pass")


@dataclass(frozen=True, slots=True)
class AgentToolCall:
    name: AgentToolName
    query: str | None = None
    document_id: str | None = None
    metadata: Mapping[str, MetadataScalar] | None = None

    def __post_init__(self) -> None:
        if self.name not in _ALLOWED_TOOLS:
            raise ValueError("tool_not_allowed")
        if self.name in {"search", "search_by_metadata"}:
            query = " ".join((self.query or "").split())
            if not 1 <= len(query) <= 2_000:
                raise ValueError("tool_query_invalid")
            object.__setattr__(self, "query", query)
        elif self.query is not None:
            raise ValueError("tool_query_not_allowed")

        if self.name == "get_document":
            document_id = (self.document_id or "").strip()
            try:
                canonical_document_id = str(UUID(document_id))
            except ValueError as exc:
                raise ValueError("tool_document_id_invalid") from exc
            if not 1 <= len(document_id) <= 200:
                raise ValueError("tool_document_id_invalid")
            object.__setattr__(self, "document_id", canonical_document_id)
        elif self.document_id is not None:
            raise ValueError("tool_document_id_not_allowed")

        if self.name == "search_by_metadata":
            metadata = dict(self.metadata or {})
            if not 1 <= len(metadata) <= 10:
                raise ValueError("tool_metadata_invalid")
            for key, value in metadata.items():
                if key not in _ALLOWED_METADATA_KEYS:
                    raise ValueError("tool_metadata_key_not_allowed")
                if (
                    not 1 <= len(key) <= 100
                    or not isinstance(value, str)
                    or not 1 <= len(value) <= 500
                ):
                    raise ValueError("tool_metadata_invalid")
            object.__setattr__(self, "metadata", metadata)
        elif self.metadata is not None:
            raise ValueError("tool_metadata_not_allowed")

    def fingerprint(self) -> str:
        return json.dumps(
            {
                "name": self.name,
                "query": self.query,
                "document_id": self.document_id,
                "metadata": self.metadata,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class AgentToolResult:
    text: str
    provenance_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentObservation:
    call: AgentToolCall
    text: str
    provenance_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgentLoopState:
    iteration: int
    observations: tuple[AgentObservation, ...]


@dataclass(frozen=True, slots=True)
class AgentAction:
    kind: Literal["tool", "finish"]
    call: AgentToolCall | None = None

    @classmethod
    def tool(cls, call: AgentToolCall) -> "AgentAction":
        return cls(kind="tool", call=call)

    @classmethod
    def finish(cls) -> "AgentAction":
        return cls(kind="finish")


@dataclass(frozen=True, slots=True)
class AgentLoopResult:
    observations: tuple[AgentObservation, ...]
    finish_reason: AgentFinishReason


@dataclass(frozen=True, slots=True)
class AgentLoopProgress:
    iteration: int
    stage: Literal["started", "completed", "failed"]
    tool: AgentToolName


AgentPlanner = Callable[[AgentLoopState], Awaitable[AgentAction]]
AgentToolExecutor = Callable[[AgentToolCall], Awaitable[AgentToolResult]]
AgentProgressSink = Callable[[AgentLoopProgress], None]


def wrap_untrusted_data(text: str, *, max_chars: int) -> str:
    escaped = html.escape(text, quote=True)[:max_chars]
    return f"<data>{escaped}</data>"


async def run_agent_loop(
    planner: AgentPlanner,
    execute_tool: AgentToolExecutor,
    *,
    max_iterations: int = 4,
    planner_timeout_seconds: float = 10.0,
    tool_timeout_seconds: float = 8.0,
    max_observation_chars: int = 16_000,
    initial_observations: tuple[AgentObservation, ...] = (),
    on_progress: AgentProgressSink | None = None,
) -> AgentLoopResult:
    """Execute an OpenRAG-owned loop without allowing provider-owned policy."""

    if not 1 <= max_iterations <= 4:
        raise ValueError("agent_iteration_limit_invalid")
    if not 0.001 <= planner_timeout_seconds <= 30:
        raise ValueError("planner_timeout_invalid")
    if not 0.001 <= tool_timeout_seconds <= 30:
        raise ValueError("tool_timeout_invalid")
    if not 1 <= max_observation_chars <= 32_000:
        raise ValueError("observation_budget_invalid")

    if len(initial_observations) > 32:
        raise ValueError("initial_observation_limit_exceeded")
    observations = list(initial_observations)
    fingerprints = {
        observation.call.fingerprint() for observation in initial_observations
    }
    consumed_chars = sum(len(item.text) for item in initial_observations)
    if consumed_chars > max_observation_chars:
        raise ValueError("initial_observation_budget_exceeded")
    for iteration in range(max_iterations):
        state = AgentLoopState(iteration=iteration, observations=tuple(observations))
        try:
            async with asyncio.timeout(planner_timeout_seconds):
                action = await planner(state)
        except TimeoutError:
            return AgentLoopResult(tuple(observations), "planner_timeout")
        except Exception:  # noqa: BLE001 - provider details remain private
            return AgentLoopResult(tuple(observations), "planner_failed")

        if action.kind == "finish":
            return AgentLoopResult(tuple(observations), "planner_finished")
        if action.kind != "tool" or action.call is None:
            return AgentLoopResult(tuple(observations), "planner_failed")

        fingerprint = action.call.fingerprint()
        if fingerprint in fingerprints:
            return AgentLoopResult(tuple(observations), "duplicate_tool_call")
        fingerprints.add(fingerprint)

        remaining = max_observation_chars - consumed_chars
        if remaining <= 0:
            return AgentLoopResult(
                tuple(observations),
                "observation_budget_exhausted",
            )
        if on_progress is not None:
            on_progress(
                AgentLoopProgress(
                    iteration=iteration + 1,
                    stage="started",
                    tool=action.call.name,
                )
            )
        try:
            async with asyncio.timeout(tool_timeout_seconds):
                result = await execute_tool(action.call)
        except TimeoutError:
            if on_progress is not None:
                on_progress(
                    AgentLoopProgress(
                        iteration=iteration + 1,
                        stage="failed",
                        tool=action.call.name,
                    )
                )
            return AgentLoopResult(tuple(observations), "tool_timeout")
        except Exception:  # noqa: BLE001 - tool details remain private
            if on_progress is not None:
                on_progress(
                    AgentLoopProgress(
                        iteration=iteration + 1,
                        stage="failed",
                        tool=action.call.name,
                    )
                )
            return AgentLoopResult(tuple(observations), "tool_failed")

        bounded_text = wrap_untrusted_data(result.text, max_chars=remaining)
        consumed_chars += min(len(html.escape(result.text, quote=True)), remaining)
        observations.append(
            AgentObservation(
                call=action.call,
                text=bounded_text,
                provenance_refs=tuple(
                    reference[:200]
                    for reference in result.provenance_refs[:100]
                    if reference
                ),
            )
        )
        if on_progress is not None:
            on_progress(
                AgentLoopProgress(
                    iteration=iteration + 1,
                    stage="completed",
                    tool=action.call.name,
                )
            )

    return AgentLoopResult(tuple(observations), "iteration_limit")
