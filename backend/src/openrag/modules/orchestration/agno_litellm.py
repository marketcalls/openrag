"""Stateless Agno adapter backed by the in-process LiteLLM Python SDK."""

from collections.abc import AsyncIterator, Callable
from typing import Protocol, cast

from agno.agent import Agent
from agno.models.litellm import LiteLLM
from agno.models.message import Message as AgnoMessage

from openrag.core.errors import UpstreamError
from openrag.modules.chat.llm import LLMDelta, LLMUsage
from openrag.modules.orchestration.model_gateway import ModelRuntime


class AgentRunner(Protocol):
    def arun(
        self,
        messages: list[AgnoMessage],
        **kwargs: object,
    ) -> AsyncIterator[object]: ...


AgentFactory = Callable[[ModelRuntime], AgentRunner]


def _default_agent(runtime: ModelRuntime) -> AgentRunner:
    request_params: dict[str, object] = {"timeout": 120.0}
    if runtime.reasoning_effort != "off":
        request_params["reasoning_effort"] = runtime.reasoning_effort
    model = LiteLLM(
        id=runtime.litellm_model,
        api_key=runtime.api_key,
        api_base=runtime.api_base,
        max_tokens=runtime.max_output_tokens,
        temperature=None,  # type: ignore[arg-type]  # Agno accepts provider omission.
        top_p=None,  # type: ignore[arg-type]  # Agno accepts provider omission.
        retries=0,
        request_params=request_params,
    )
    return cast(
        AgentRunner,
        Agent(
            model=model,
            tools=[],
            build_context=False,
            add_history_to_context=False,
            search_knowledge=False,
            reasoning=False,
            markdown=False,
            telemetry=False,
            store_events=False,
        ),
    )


class AgnoLiteLLMStreamer:
    """Expose only safe text deltas and aggregate usage to chat services."""

    def __init__(
        self,
        runtime: ModelRuntime,
        *,
        agent_factory: AgentFactory = _default_agent,
    ) -> None:
        self._runtime = runtime
        self._agent_factory = agent_factory

    async def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[LLMDelta | LLMUsage]:
        del model  # The request-scoped runtime is authoritative.
        agno_messages = [
            AgnoMessage(role=message["role"], content=message["content"])
            for message in messages
        ]
        try:
            agent = self._agent_factory(self._runtime)
            events = agent.arun(
                agno_messages,
                stream=True,
                stream_events=True,
            )
            async for event in events:
                event_name = getattr(event, "event", None)
                if event_name == "RunContent":
                    content = getattr(event, "content", None)
                    if not isinstance(content, str):
                        raise UpstreamError("model execution failed")
                    if content:
                        yield LLMDelta(content)
                elif event_name == "RunCompleted":
                    metrics = getattr(event, "metrics", None)
                    yield LLMUsage(
                        prompt_tokens=int(
                            getattr(metrics, "input_tokens", 0) or 0
                        ),
                        completion_tokens=int(
                            getattr(metrics, "output_tokens", 0) or 0
                        ),
                        estimated_cost_microusd=max(
                            0,
                            round(
                                float(getattr(metrics, "cost", 0) or 0)
                                * 1_000_000
                            ),
                        ),
                    )
                elif event_name in {"RunError", "RunCancelled"}:
                    raise UpstreamError("model execution failed")
        except UpstreamError:
            raise
        except Exception as exc:
            raise UpstreamError("model execution failed") from exc
