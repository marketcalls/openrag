from collections.abc import AsyncIterator
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from openrag.modules.models.capability_probe import (
    CapabilityProbeResult,
    apply_probe_result,
    build_probe_claim_query,
    probe_model_capabilities,
)
from openrag.modules.models.models import Model, ModelProbe
from openrag.modules.orchestration.model_gateway import ModelRuntime


class _Stream:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    async def __aiter__(self) -> AsyncIterator[object]:
        for value in self._values:
            yield value


def _response(*, content: str = "", tool_name: str | None = None) -> object:
    tool_calls = []
    if tool_name is not None:
        tool_calls = [
            SimpleNamespace(
                function=SimpleNamespace(name=tool_name, arguments='{"value":"ok"}')
            )
        ]
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls)
            )
        ]
    )


async def test_probe_measures_stream_json_tools_vision_and_context_without_globals() -> None:
    calls: list[dict[str, object]] = []

    async def completion(**kwargs: object) -> object:
        calls.append(dict(kwargs))
        if kwargs.get("stream") is True:
            return _Stream(
                [
                    SimpleNamespace(
                        choices=[SimpleNamespace(delta=SimpleNamespace(content="OPENRAG"))]
                    ),
                    SimpleNamespace(
                        choices=[SimpleNamespace(delta=SimpleNamespace(content="_OK"))]
                    ),
                ]
            )
        if "reasoning_effort" in kwargs:
            return _response(content="reasoning accepted")
        if "response_format" in kwargs:
            return _response(content='{"status":"ok"}')
        if "tools" in kwargs:
            return _response(tool_name="openrag_probe")
        return _response(content="image received")

    runtime = ModelRuntime(
        litellm_model="openai/probe-model",
        api_key="write-only-secret",
        api_base="https://models.example.test/v1",
        max_output_tokens=128,
    )
    result = await probe_model_capabilities(
        runtime,
        completion=completion,
        model_info=lambda **_kwargs: {
            "max_input_tokens": 131_072,
            "supports_reasoning": True,
        },
    )

    assert result.error_code is None
    assert result.supports_chat_completion is True
    assert result.supports_streaming is True
    assert result.supports_structured_json is True
    assert result.supports_tools is True
    assert result.supports_vision is True
    assert result.supports_reasoning is True
    assert result.context_window == 131_072
    assert 0 <= result.latency_ms <= 120_000
    assert len(calls) == 5
    assert all(call["model"] == "openai/probe-model" for call in calls)
    assert all(call["api_key"] == "write-only-secret" for call in calls)
    assert all(call["base_url"] == "https://models.example.test/v1" for call in calls)
    assert all(int(call["max_tokens"]) <= 32 for call in calls)


async def test_probe_fails_closed_with_safe_error_code_and_no_optional_calls() -> None:
    calls = 0

    async def completion(**_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise TimeoutError("provider payload with secret sk-do-not-store")

    result = await probe_model_capabilities(
        ModelRuntime(
            litellm_model="ollama/local",
            api_key=None,
            api_base="http://ollama:11434",
            max_output_tokens=128,
        ),
        completion=completion,
        model_info=lambda **_kwargs: {"max_input_tokens": 8192},
    )

    assert calls == 1
    assert result.error_code == "provider_timeout"
    assert result.supports_chat_completion is False
    assert result.supports_streaming is False
    assert result.supports_structured_json is False
    assert result.supports_tools is False
    assert result.supports_vision is False
    assert result.supports_reasoning is False
    assert result.context_window is None


async def test_optional_probe_failures_do_not_invalidate_working_chat() -> None:
    async def completion(**kwargs: object) -> object:
        if kwargs.get("stream") is True:
            return _Stream(
                [
                    SimpleNamespace(
                        choices=[SimpleNamespace(delta=SimpleNamespace(content="ok"))]
                    )
                ]
            )
        raise RuntimeError("unsupported optional capability")

    result = await probe_model_capabilities(
        ModelRuntime(
            litellm_model="openai/minimal",
            api_key="secret",
            api_base=None,
            max_output_tokens=128,
        ),
        completion=completion,
        model_info=lambda **_kwargs: {},
    )

    assert result.error_code is None
    assert result.supports_chat_completion is True
    assert result.supports_streaming is True
    assert result.supports_structured_json is False
    assert result.supports_tools is False
    assert result.supports_vision is False
    assert result.supports_reasoning is False
    assert result.context_window is None


def test_latest_successful_probe_is_the_only_result_allowed_to_enable_model() -> None:
    model = Model(
        id=uuid4(),
        litellm_model_name="gpt-5-mini",
        display_name="GPT-5 mini",
        provider_kind="openai",
        probe_revision=2,
    )
    current = ModelProbe(
        id=uuid4(),
        model_id=model.id,
        revision=2,
        configuration_fingerprint="a" * 64,
        status="running",
    )
    result = CapabilityProbeResult(
        supports_chat_completion=True,
        supports_streaming=True,
        supports_structured_json=True,
        supports_tools=True,
        supports_vision=False,
        supports_reasoning=True,
        context_window=128_000,
        latency_ms=432,
        error_code=None,
    )

    assert apply_probe_result(model, current, result) is True
    assert current.status == "passed"
    assert model.probe_status == "passed"
    assert model.supports_chat_completion is True
    assert model.supports_streaming is True
    assert model.supports_verifier is True
    assert model.supports_tools is True
    assert model.supports_reasoning is True
    assert model.context_window == 128_000
    assert model.probe_latency_ms == 432

    stale = ModelProbe(
        id=uuid4(),
        model_id=model.id,
        revision=1,
        configuration_fingerprint="b" * 64,
        status="running",
    )
    assert apply_probe_result(model, stale, result) is False
    assert stale.status == "stale"
    assert model.probe_revision == 2


def test_failed_current_probe_keeps_every_measured_capability_disabled() -> None:
    model = Model(
        id=uuid4(),
        litellm_model_name="missing-model",
        display_name="Missing",
        provider_kind="openai",
        probe_revision=1,
    )
    probe = ModelProbe(
        id=uuid4(),
        model_id=model.id,
        revision=1,
        configuration_fingerprint="c" * 64,
        status="running",
    )
    result = CapabilityProbeResult(
        supports_chat_completion=False,
        supports_streaming=False,
        supports_structured_json=False,
        supports_tools=False,
        supports_vision=False,
        supports_reasoning=False,
        context_window=None,
        latency_ms=25,
        error_code="model_not_found",
    )

    assert apply_probe_result(model, probe, result) is True
    assert probe.status == "failed"
    assert model.probe_status == "failed"
    assert model.last_probe_error_code == "model_not_found"
    assert model.supports_chat_completion is False
    assert model.supports_streaming is False
    assert model.supports_structured_json is False
    assert model.supports_verifier is False
    assert model.supports_tools is False
    assert model.supports_vision is False
    assert model.supports_reasoning is False


def test_probe_claim_query_is_parallel_worker_safe_and_bounded() -> None:
    statement = build_probe_claim_query()
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).upper()

    assert "FOR UPDATE SKIP LOCKED" in compiled
    assert "LIMIT 1" in compiled
    assert "MODEL_PROBES.STATUS = 'QUEUED'" in compiled
    assert "MODEL_PROBES.LEASE_EXPIRES_AT" in compiled
