"""Agno adapter for measured JSON-schema grounded-answer verification."""

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Protocol, cast

from agno.agent import Agent
from agno.models.litellm import LiteLLM
from agno.models.message import Message as AgnoMessage
from pydantic import BaseModel, ValidationError

from openrag.core.errors import UpstreamError
from openrag.modules.chat.llm import LLMDelta, LLMUsage
from openrag.modules.orchestration.answer_validation import VerifierOutput
from openrag.modules.orchestration.model_gateway import ModelRuntime


class VerifierRunner(Protocol):
    def arun(self, value: object, **kwargs: object) -> Awaitable[object]: ...


VerifierRunnerFactory = Callable[[ModelRuntime], VerifierRunner]


def _default_runner(runtime: ModelRuntime) -> VerifierRunner:
    model = LiteLLM(
        id=runtime.litellm_model,
        api_key=runtime.api_key,
        api_base=runtime.api_base,
        max_tokens=min(runtime.max_output_tokens, 512),
        temperature=None,
        top_p=None,
        retries=0,
        request_params={"timeout": 30.0},
    )
    return cast(
        VerifierRunner,
        Agent(
            model=model,
            tools=[],
            output_schema=VerifierOutput,
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


def _validated_content(value: object) -> VerifierOutput:
    if isinstance(value, VerifierOutput):
        return value
    if isinstance(value, BaseModel):
        return VerifierOutput.model_validate(value.model_dump())
    if isinstance(value, Mapping):
        return VerifierOutput.model_validate(dict(value))
    if isinstance(value, str):
        return VerifierOutput.model_validate_json(value)
    raise ValueError("verifier_output_invalid")


class AgnoStructuredVerifierStreamer:
    """Expose schema-validated verifier JSON through the existing protocol."""

    def __init__(
        self,
        runtime: ModelRuntime,
        *,
        runner_factory: VerifierRunnerFactory = _default_runner,
    ) -> None:
        self._runner = runner_factory(runtime)

    async def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[LLMDelta | LLMUsage]:
        del model
        agno_messages = [
            AgnoMessage(role=message["role"], content=message["content"])
            for message in messages
        ]
        try:
            response = await self._runner.arun(agno_messages, stream=False)
            output = _validated_content(getattr(response, "content", response))
            yield LLMDelta(output.model_dump_json())
            metrics = getattr(response, "metrics", None)
            yield LLMUsage(
                prompt_tokens=int(getattr(metrics, "input_tokens", 0) or 0),
                completion_tokens=int(getattr(metrics, "output_tokens", 0) or 0),
                estimated_cost_microusd=max(
                    0,
                    round(float(getattr(metrics, "cost", 0) or 0) * 1_000_000),
                ),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise UpstreamError("answer verification failed") from exc
        except UpstreamError:
            raise
        except Exception as exc:
            raise UpstreamError("answer verification failed") from exc
