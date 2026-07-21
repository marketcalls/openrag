from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from openrag.core.errors import UpstreamError
from openrag.modules.chat.llm import LLMDelta, LLMUsage
from openrag.modules.orchestration.agno_litellm import (
    AgnoLiteLLMStreamer,
    _default_agent,
)
from openrag.modules.orchestration.model_gateway import ModelRuntime


class FakeAgent:
    def __init__(self, events: list[object]) -> None:
        self.events = events
        self.calls: list[dict[str, object]] = []

    def arun(self, messages: list[object], **kwargs: object) -> AsyncIterator[object]:
        self.calls.append({"messages": messages, **kwargs})

        async def iterate() -> AsyncIterator[object]:
            for event in self.events:
                yield event

        return iterate()


def runtime() -> ModelRuntime:
    return ModelRuntime(
        litellm_model="openai/gpt-5-mini",
        api_key="sk-secret",
        api_base=None,
        max_output_tokens=2_048,
        reasoning_effort="off",
    )


def test_reasoning_effort_is_forwarded_only_when_enabled() -> None:
    off_agent = _default_agent(runtime())
    assert off_agent.model.request_params == {"timeout": 120.0}
    assert off_agent.model.temperature is None
    assert off_agent.model.top_p is None

    high_agent = _default_agent(
        ModelRuntime(
            litellm_model="openai/gpt-5-mini",
            api_key="sk-secret",
            api_base=None,
            max_output_tokens=2_048,
            reasoning_effort="high",
        )
    )
    assert high_agent.model.request_params == {
        "timeout": 120.0,
        "reasoning_effort": "high",
    }
    assert high_agent.model.temperature is None
    assert high_agent.model.top_p is None


async def collect(streamer: AgnoLiteLLMStreamer) -> list[LLMDelta | LLMUsage]:
    return [
        item
        async for item in streamer.stream(
            model="ignored-by-request-scoped-runtime",
            messages=[
                {"role": "system", "content": "Policy"},
                {"role": "user", "content": "hi"},
            ],
        )
    ]


async def test_streams_only_public_content_and_terminal_usage() -> None:
    metrics = SimpleNamespace(input_tokens=11, output_tokens=4)
    agent = FakeAgent(
        [
            SimpleNamespace(event="RunStarted"),
            SimpleNamespace(event="RunContent", content="Hel"),
            SimpleNamespace(event="RunContent", content="lo"),
            SimpleNamespace(event="RunCompleted", metrics=metrics),
        ]
    )
    streamer = AgnoLiteLLMStreamer(runtime(), agent_factory=lambda _runtime: agent)

    items = await collect(streamer)

    assert items == [LLMDelta("Hel"), LLMDelta("lo"), LLMUsage(11, 4)]
    assert agent.calls[0]["stream"] is True
    assert agent.calls[0]["stream_events"] is True
    messages = cast(list[Any], agent.calls[0]["messages"])
    assert [message.role for message in messages] == ["system", "user"]
    assert [message.content for message in messages] == ["Policy", "hi"]


@pytest.mark.parametrize(
    "event",
    [
        SimpleNamespace(event="RunError", content="raw provider secret"),
        SimpleNamespace(event="RunCancelled"),
    ],
)
async def test_agent_failures_are_sanitized(event: Any) -> None:
    agent = FakeAgent([event])
    streamer = AgnoLiteLLMStreamer(runtime(), agent_factory=lambda _runtime: agent)

    with pytest.raises(UpstreamError, match="model execution failed") as captured:
        await collect(streamer)

    assert "raw provider secret" not in str(captured.value)


async def test_unexpected_adapter_exception_is_sanitized() -> None:
    class BrokenAgent:
        def arun(self, *_args: object, **_kwargs: object) -> AsyncIterator[object]:
            raise RuntimeError("sk-secret leaked by provider")

    streamer = AgnoLiteLLMStreamer(
        runtime(),
        agent_factory=lambda _runtime: BrokenAgent(),
    )

    with pytest.raises(UpstreamError, match="model execution failed") as captured:
        await collect(streamer)

    assert "sk-secret" not in str(captured.value)


def test_api_route_does_not_import_agno_litellm_or_provider_sdks() -> None:
    route = (
        Path(__file__).parents[3]
        / "src"
        / "openrag"
        / "api"
        / "routes"
        / "chats.py"
    ).read_text(encoding="utf-8")

    assert "import agno" not in route
    assert "from agno" not in route
    assert "import litellm" not in route
    assert "from litellm" not in route
    assert "openai" not in route.casefold()
