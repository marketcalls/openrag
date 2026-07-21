from collections.abc import Awaitable
from types import SimpleNamespace

import pytest

from openrag.core.errors import UpstreamError
from openrag.modules.artifacts.prompting import AnalyticsEvidence
from openrag.modules.orchestration.agno_analytics import (
    AgnoAnalyticsComposer,
    _default_runner,
)
from openrag.modules.orchestration.model_gateway import ModelRuntime


def artifact_payload(*, marker: int = 1) -> dict[str, object]:
    return {
        "schema_version": "analytics.v1",
        "title": "Revenue dashboard",
        "subtitle": "Approved Q4 summary",
        "kpis": [
            {
                "label": "Q4 revenue",
                "value": "$4.83M",
                "trend": "up",
                "source_markers": [marker],
            }
        ],
        "blocks": [
            {
                "kind": "bar_chart",
                "title": "Monthly revenue",
                "x_label": "Month",
                "y_label": "Revenue in millions",
                "categories": ["October", "November", "December"],
                "series": [
                    {"name": "Revenue", "values": [1.42, 1.57, 1.84]}
                ],
                "source_markers": [marker],
            }
        ],
        "suggested_followups": ["Break this down by product line"],
    }


class Runner:
    def __init__(self, content: object) -> None:
        self.content = content
        self.calls: list[tuple[object, dict[str, object]]] = []

    def arun(self, value: object, **kwargs: object) -> Awaitable[object]:
        self.calls.append((value, kwargs))

        async def result() -> object:
            return SimpleNamespace(
                content=self.content,
                metrics=SimpleNamespace(
                    input_tokens=120,
                    output_tokens=44,
                    cost=0.000321,
                ),
            )

        return result()


def runtime() -> ModelRuntime:
    return ModelRuntime(
        litellm_model="openai/gpt-5-mini",
        api_key="write-only-secret",
        api_base=None,
        max_output_tokens=8_192,
    )


def test_default_composer_is_bounded_stateless_and_schema_only() -> None:
    runner = _default_runner(runtime())

    assert runner.model.max_tokens == 4_096  # type: ignore[attr-defined]
    assert runner.model.temperature is None  # type: ignore[attr-defined]
    assert runner.model.retries == 0  # type: ignore[attr-defined]
    assert runner.model.request_params == {"timeout": 45.0}  # type: ignore[attr-defined]
    assert runner.tools == []  # type: ignore[attr-defined]
    assert runner.structured_outputs is True  # type: ignore[attr-defined]
    assert runner.parse_response is True  # type: ignore[attr-defined]
    assert runner.telemetry is False  # type: ignore[attr-defined]
    assert runner.store_events is False  # type: ignore[attr-defined]


async def test_composer_returns_only_validated_artifact_and_usage() -> None:
    runner = Runner(artifact_payload())
    composer = AgnoAnalyticsComposer(
        runtime(),
        runner_factory=lambda _runtime: runner,
    )

    result = await composer.compose(
        question="Show Q4 revenue as a chart",
        answer_markdown="Q4 revenue was $4.83M [1].",
        evidence=(
            AnalyticsEvidence(marker=1, text="Q4 revenue was $4.83M."),
        ),
        allowed_markers=(1,),
    )

    assert result.artifact.schema_version == "analytics.v1"
    assert result.artifact.blocks[0].source_markers == [1]
    assert result.usage.prompt_tokens == 120
    assert result.usage.completion_tokens == 44
    assert result.usage.estimated_cost_microusd == 321
    assert runner.calls[0][1] == {"stream": False}
    assert "write-only-secret" not in repr(runner.calls)


@pytest.mark.parametrize(
    "provider_output",
    [
        artifact_payload(marker=2),
        {**artifact_payload(), "component": "UnsafeWidget"},
        "not-json",
    ],
)
async def test_composer_rejects_unavailable_markers_or_invalid_output(
    provider_output: object,
) -> None:
    composer = AgnoAnalyticsComposer(
        runtime(),
        runner_factory=lambda _runtime: Runner(provider_output),
    )

    with pytest.raises(UpstreamError, match="analytics composition failed"):
        await composer.compose(
            question="Show Q4 revenue as a chart",
            answer_markdown="Q4 revenue was $4.83M [1].",
            evidence=(
                AnalyticsEvidence(marker=1, text="Q4 revenue was $4.83M."),
            ),
            allowed_markers=(1,),
        )


async def test_composer_sanitizes_provider_failures() -> None:
    class BrokenRunner:
        async def arun(self, value: object, **kwargs: object) -> object:
            del value, kwargs
            raise RuntimeError("secret provider traceback sk-sensitive")

    composer = AgnoAnalyticsComposer(
        runtime(),
        runner_factory=lambda _runtime: BrokenRunner(),
    )

    with pytest.raises(UpstreamError) as raised:
        await composer.compose(
            question="Show Q4 revenue",
            answer_markdown="Q4 revenue was $4.83M [1].",
            evidence=(AnalyticsEvidence(marker=1, text="Revenue was $4.83M."),),
            allowed_markers=(1,),
        )
    assert str(raised.value) == "analytics composition failed"
    assert "sensitive" not in str(raised.value)


async def test_composer_rewraps_upstream_errors_without_provider_details() -> None:
    class BrokenRunner:
        async def arun(self, value: object, **kwargs: object) -> object:
            del value, kwargs
            raise UpstreamError("gateway leaked sk-sensitive")

    composer = AgnoAnalyticsComposer(
        runtime(),
        runner_factory=lambda _runtime: BrokenRunner(),
    )

    with pytest.raises(UpstreamError) as raised:
        await composer.compose(
            question="Show Q4 revenue",
            answer_markdown="Q4 revenue was $4.83M [1].",
            evidence=(AnalyticsEvidence(marker=1, text="Revenue was $4.83M."),),
            allowed_markers=(1,),
        )
    assert str(raised.value) == "analytics composition failed"
    assert "sensitive" not in str(raised.value)
