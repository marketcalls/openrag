from collections.abc import Awaitable
from types import SimpleNamespace

import pytest

from openrag.core.errors import UpstreamError
from openrag.modules.chat.llm import LLMDelta, LLMUsage
from openrag.modules.orchestration.agno_validator import AgnoStructuredVerifierStreamer
from openrag.modules.orchestration.model_gateway import ModelRuntime


class Runner:
    def __init__(self, content: object) -> None:
        self.content = content
        self.calls: list[tuple[object, dict[str, object]]] = []

    def arun(self, value: object, **kwargs: object) -> Awaitable[object]:
        self.calls.append((value, kwargs))

        async def result() -> object:
            return SimpleNamespace(
                content=self.content,
                metrics=SimpleNamespace(input_tokens=9, output_tokens=2, cost=0.000003),
            )

        return result()


def _runtime() -> ModelRuntime:
    return ModelRuntime(
        litellm_model="openai/verifier",
        api_key="secret",
        api_base=None,
        max_output_tokens=512,
    )


async def test_adapter_emits_only_schema_valid_json_and_usage() -> None:
    runner = Runner(
        {
            "grounded": True,
            "grounding_score": 0.97,
            "completeness_score": 0.91,
        }
    )
    streamer = AgnoStructuredVerifierStreamer(
        _runtime(),
        runner_factory=lambda _runtime: runner,
    )

    events = [
        event
        async for event in streamer.stream(
            model="ignored",
            messages=[{"role": "user", "content": "<data>untrusted</data>"}],
        )
    ]

    assert events == [
        LLMDelta(
            '{"grounded":true,"grounding_score":0.97,"completeness_score":0.91}'
        ),
        LLMUsage(prompt_tokens=9, completion_tokens=2, estimated_cost_microusd=3),
    ]
    assert runner.calls[0][1] == {"stream": False}


async def test_adapter_rejects_extra_or_malformed_provider_fields() -> None:
    runner = Runner(
        {
            "grounded": True,
            "grounding_score": 1.0,
            "completeness_score": 1.0,
            "reasoning": "must not escape",
        }
    )
    streamer = AgnoStructuredVerifierStreamer(
        _runtime(),
        runner_factory=lambda _runtime: runner,
    )

    with pytest.raises(UpstreamError, match="verification failed"):
        _ = [
            event
            async for event in streamer.stream(
                model="ignored",
                messages=[{"role": "user", "content": "data"}],
            )
        ]
