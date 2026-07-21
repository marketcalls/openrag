"""Schema-bound Agno composer for citation-grounded analytics artifacts."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from agno.agent import Agent
from agno.models.litellm import LiteLLM
from agno.models.message import Message as AgnoMessage
from pydantic import BaseModel, ValidationError

from openrag.core.errors import UpstreamError
from openrag.modules.artifacts.prompting import (
    AnalyticsEvidence,
    build_analytics_messages,
)
from openrag.modules.artifacts.schemas import AnalyticsResponseV1
from openrag.modules.chat.llm import LLMUsage
from openrag.modules.orchestration.model_gateway import ModelRuntime


@dataclass(frozen=True, slots=True)
class AnalyticsComposition:
    artifact: AnalyticsResponseV1
    usage: LLMUsage


class AnalyticsComposer(Protocol):
    async def compose(
        self,
        *,
        question: str,
        answer_markdown: str,
        evidence: tuple[AnalyticsEvidence, ...],
        allowed_markers: tuple[int, ...],
    ) -> AnalyticsComposition: ...


class AnalyticsRunner(Protocol):
    def arun(self, value: object, **kwargs: object) -> Awaitable[object]: ...


AnalyticsRunnerFactory = Callable[[ModelRuntime], AnalyticsRunner]


def _default_runner(runtime: ModelRuntime) -> AnalyticsRunner:
    model = LiteLLM(
        id=runtime.litellm_model,
        api_key=runtime.api_key,
        api_base=runtime.api_base,
        max_tokens=min(runtime.max_output_tokens, 4_096),
        temperature=None,  # type: ignore[arg-type]  # Agno accepts provider omission.
        top_p=None,  # type: ignore[arg-type]  # Agno accepts provider omission.
        retries=0,
        request_params={"timeout": 45.0},
    )
    return cast(
        AnalyticsRunner,
        Agent(
            model=model,
            tools=[],
            output_schema=AnalyticsResponseV1,
            structured_outputs=True,
            parse_response=True,
            build_context=False,
            add_history_to_context=False,
            search_knowledge=False,
            reasoning=False,
            markdown=False,
            telemetry=False,
            store_events=False,
        ),
    )


def _validated_content(value: object) -> AnalyticsResponseV1:
    if isinstance(value, AnalyticsResponseV1):
        return value
    if isinstance(value, BaseModel):
        return AnalyticsResponseV1.model_validate(value.model_dump())
    if isinstance(value, Mapping):
        return AnalyticsResponseV1.model_validate(dict(value))
    if isinstance(value, str):
        return AnalyticsResponseV1.model_validate_json(value)
    raise ValueError("analytics_output_invalid")


def _source_markers(artifact: AnalyticsResponseV1) -> frozenset[int]:
    markers: set[int] = set()
    for kpi in artifact.kpis:
        markers.update(kpi.source_markers)
    for block in artifact.blocks:
        markers.update(block.source_markers)
    return frozenset(markers)


class AgnoAnalyticsComposer:
    """Use measured structured output only for supplemental presentation."""

    def __init__(
        self,
        runtime: ModelRuntime,
        *,
        runner_factory: AnalyticsRunnerFactory = _default_runner,
    ) -> None:
        self._runner = runner_factory(runtime)

    async def compose(
        self,
        *,
        question: str,
        answer_markdown: str,
        evidence: tuple[AnalyticsEvidence, ...],
        allowed_markers: tuple[int, ...],
    ) -> AnalyticsComposition:
        try:
            messages = build_analytics_messages(
                question=question,
                answer_markdown=answer_markdown,
                evidence=evidence,
                allowed_markers=allowed_markers,
            )
            agno_messages = [
                AgnoMessage(role=message["role"], content=message["content"])
                for message in messages
            ]
            response = await self._runner.arun(agno_messages, stream=False)
            artifact = _validated_content(getattr(response, "content", response))
            if not _source_markers(artifact).issubset(frozenset(allowed_markers)):
                raise ValueError("analytics_output_marker_invalid")
            metrics = getattr(response, "metrics", None)
            usage = LLMUsage(
                prompt_tokens=max(0, int(getattr(metrics, "input_tokens", 0) or 0)),
                completion_tokens=max(
                    0,
                    int(getattr(metrics, "output_tokens", 0) or 0),
                ),
                estimated_cost_microusd=max(
                    0,
                    round(float(getattr(metrics, "cost", 0) or 0) * 1_000_000),
                ),
            )
            return AnalyticsComposition(artifact=artifact, usage=usage)
        except UpstreamError as exc:
            raise UpstreamError("analytics composition failed") from exc
        except (ValidationError, ValueError, TypeError) as exc:
            raise UpstreamError("analytics composition failed") from exc
        except Exception as exc:
            raise UpstreamError("analytics composition failed") from exc
