"""Bounded live capability checks through the in-process LiteLLM SDK."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Mapping
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter_ns
from typing import Protocol, cast, runtime_checkable

from sqlalchemy import Select, or_, select

from openrag.core.db import naive_utc
from openrag.modules.models.models import Model, ModelProbe
from openrag.modules.orchestration.model_gateway import ModelRuntime

_PROBE_TIMEOUT_SECONDS = 15.0
_PROBE_TIMEOUT_GRACE_SECONDS = 3.0
_PROBE_MAX_TOKENS = 32
_VISION_PIXEL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)


class CompletionCall(Protocol):
    def __call__(self, **kwargs: object) -> Awaitable[object]: ...


class ModelInfoCall(Protocol):
    def __call__(self, **kwargs: object) -> object: ...


@runtime_checkable
class AsyncObjectStream(Protocol):
    def __aiter__(self) -> AsyncIterator[object]: ...


@dataclass(frozen=True, slots=True)
class CapabilityProbeResult:
    supports_chat_completion: bool
    supports_streaming: bool
    supports_structured_json: bool
    supports_tools: bool
    supports_vision: bool
    supports_reasoning: bool
    context_window: int | None
    latency_ms: int
    error_code: str | None


def build_probe_claim_query(
    now: datetime | None = None,
) -> Select[tuple[ModelProbe]]:
    """Build a bounded claim query safe for parallel probe workers."""

    current = now or naive_utc()
    return (
        select(ModelProbe)
        .where(
            or_(
                ModelProbe.status == "queued",
                (
                    (ModelProbe.status == "running")
                    & (ModelProbe.lease_expires_at < current)
                ),
            ),
            ModelProbe.attempts < 3,
        )
        .order_by(ModelProbe.created_at, ModelProbe.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def apply_probe_result(
    model: Model,
    probe: ModelProbe,
    result: CapabilityProbeResult,
) -> bool:
    """Apply a safe result only when its model revision is still authoritative."""

    completed_at = naive_utc()
    probe.supports_chat_completion = result.supports_chat_completion
    probe.supports_streaming = result.supports_streaming
    probe.supports_structured_json = result.supports_structured_json
    probe.supports_tools = result.supports_tools
    probe.supports_vision = result.supports_vision
    probe.supports_reasoning = result.supports_reasoning
    probe.context_window = result.context_window
    probe.latency_ms = result.latency_ms
    probe.error_code = result.error_code
    probe.completed_at = completed_at
    probe.lease_owner = None
    probe.lease_token = None
    probe.lease_expires_at = None
    if probe.revision != model.probe_revision:
        probe.status = "stale"
        return False

    passed = (
        result.error_code is None
        and result.supports_chat_completion
        and result.supports_streaming
    )
    probe.status = "passed" if passed else "failed"
    model.probe_status = "passed" if passed else "failed"
    model.probe_latency_ms = result.latency_ms
    model.last_probe_error_code = None if passed else (
        result.error_code or "probe_contract_failed"
    )
    model.last_probed_at = completed_at
    model.supports_chat_completion = passed
    model.supports_streaming = passed
    model.supports_structured_json = passed and result.supports_structured_json
    model.supports_verifier = passed and result.supports_structured_json
    model.supports_tools = passed and result.supports_tools
    model.supports_vision = passed and result.supports_vision
    model.supports_reasoning = passed and result.supports_reasoning
    if not model.supports_reasoning:
        model.default_reasoning_effort = "off"
    model.context_window = result.context_window if passed else None
    return True


def _field(value: object, name: str) -> object | None:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _first_choice(response: object) -> object | None:
    choices = _field(response, "choices")
    if not isinstance(choices, list) or not choices:
        return None
    return cast(object, choices[0])


def _message(response: object) -> object | None:
    choice = _first_choice(response)
    return _field(choice, "message") if choice is not None else None


def _message_content(response: object) -> str:
    message = _message(response)
    content = _field(message, "content") if message is not None else None
    return content if isinstance(content, str) else ""


async def _stream_text(stream: object) -> str:
    if not isinstance(stream, AsyncObjectStream):
        return ""
    parts: list[str] = []
    async for chunk in stream:
        choice = _first_choice(chunk)
        delta = _field(choice, "delta") if choice is not None else None
        content = _field(delta, "content") if delta is not None else None
        if isinstance(content, str):
            parts.append(content)
    return "".join(parts)


def _tool_called(response: object) -> bool:
    message = _message(response)
    calls = _field(message, "tool_calls") if message is not None else None
    if not isinstance(calls, list) or len(calls) != 1:
        return False
    function = _field(calls[0], "function")
    name = _field(function, "name") if function is not None else None
    arguments = _field(function, "arguments") if function is not None else None
    if name != "openrag_probe" or not isinstance(arguments, str):
        return False
    try:
        parsed: object = json.loads(arguments)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict) and parsed.get("value") == "ok"


def _structured_response(response: object) -> bool:
    try:
        parsed: object = json.loads(_message_content(response))
    except json.JSONDecodeError:
        return False
    return parsed == {"status": "ok"}


def _context_window(info: object) -> int | None:
    for name in ("max_input_tokens", "max_tokens"):
        raw = _field(info, name)
        if isinstance(raw, int) and not isinstance(raw, bool) and 1 <= raw <= 10_000_000:
            return raw
    return None


def _safe_error_code(exc: BaseException) -> str:
    name = type(exc).__name__.casefold()
    if "timeout" in name:
        return "provider_timeout"
    if "authentication" in name or "permission" in name:
        return "provider_authentication_failed"
    if "ratelimit" in name:
        return "provider_rate_limited"
    if "notfound" in name:
        return "model_not_found"
    if "connection" in name or "serviceunavailable" in name:
        return "provider_unavailable"
    return "provider_rejected"


def _request(runtime: ModelRuntime) -> dict[str, object]:
    return {
        "model": runtime.litellm_model,
        "api_key": runtime.api_key,
        "base_url": runtime.api_base,
        "timeout": _PROBE_TIMEOUT_SECONDS,
        "max_tokens": min(runtime.max_output_tokens, _PROBE_MAX_TOKENS),
    }


async def _call(
    completion: CompletionCall,
    request: dict[str, object],
) -> object:
    async with asyncio.timeout(
        _PROBE_TIMEOUT_SECONDS + _PROBE_TIMEOUT_GRACE_SECONDS
    ):
        return await completion(**request)


async def _default_completion(**kwargs: object) -> object:
    from litellm import acompletion

    return await acompletion(**kwargs)


def _default_model_info(**kwargs: object) -> object:
    from litellm import get_model_info

    return get_model_info(**kwargs)


async def probe_model_capabilities(
    runtime: ModelRuntime,
    *,
    completion: CompletionCall = _default_completion,
    model_info: ModelInfoCall = _default_model_info,
) -> CapabilityProbeResult:
    """Measure bounded capabilities; optional failures do not expose details."""

    started = perf_counter_ns()
    base = _request(runtime)
    try:
        stream = await _call(
            completion,
            {
                **base,
                "messages": [
                    {
                        "role": "user",
                        "content": "Reply with exactly OPENRAG_OK.",
                    }
                ],
                "stream": True,
            },
        )
        streamed_text = await _stream_text(stream)
        supports_streaming = bool(streamed_text.strip())
        if not supports_streaming:
            raise RuntimeError("empty_probe_stream")
    except Exception as exc:  # noqa: BLE001 - converted to a bounded safe code
        return CapabilityProbeResult(
            supports_chat_completion=False,
            supports_streaming=False,
            supports_structured_json=False,
            supports_tools=False,
            supports_vision=False,
            supports_reasoning=False,
            context_window=None,
            latency_ms=max(0, (perf_counter_ns() - started) // 1_000_000),
            error_code=_safe_error_code(exc),
        )

    supports_structured_json = False
    try:
        response = await _call(
            completion,
            {
                **base,
                "messages": [
                    {
                        "role": "user",
                        "content": "Return the required probe JSON.",
                    }
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "openrag_capability_probe",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "status": {"type": "string", "enum": ["ok"]}
                            },
                            "required": ["status"],
                            "additionalProperties": False,
                        },
                    },
                },
            },
        )
        supports_structured_json = _structured_response(response)
    except Exception:  # noqa: BLE001 - an unsupported optional capability is false
        supports_structured_json = False

    supports_tools = False
    try:
        response = await _call(
            completion,
            {
                **base,
                "messages": [
                    {
                        "role": "user",
                        "content": "Call openrag_probe with value ok.",
                    }
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "openrag_probe",
                            "description": "Bounded OpenRAG capability test.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "value": {"type": "string", "enum": ["ok"]}
                                },
                                "required": ["value"],
                                "additionalProperties": False,
                            },
                        },
                    }
                ],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "openrag_probe"},
                },
            },
        )
        supports_tools = _tool_called(response)
    except Exception:  # noqa: BLE001 - an unsupported optional capability is false
        supports_tools = False

    supports_vision = False
    try:
        response = await _call(
            completion,
            {
                **base,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Acknowledge this image."},
                            {
                                "type": "image_url",
                                "image_url": {"url": _VISION_PIXEL},
                            },
                        ],
                    }
                ],
            },
        )
        supports_vision = bool(_message_content(response).strip())
    except Exception:  # noqa: BLE001 - an unsupported optional capability is false
        supports_vision = False

    try:
        info = model_info(
            model=runtime.litellm_model,
            api_base=runtime.api_base,
            api_key=runtime.api_key,
        )
        context_window = _context_window(info)
    except Exception:  # noqa: BLE001 - metadata absence cannot fail a live probe
        info = {}
        context_window = None

    supports_reasoning = False
    if _field(info, "supports_reasoning") is True:
        try:
            response = await _call(
                completion,
                {
                    **base,
                    "messages": [
                        {
                            "role": "user",
                            "content": "Reply briefly to confirm reasoning mode.",
                        }
                    ],
                    "reasoning_effort": "low",
                },
            )
            supports_reasoning = bool(_message_content(response).strip())
        except Exception:  # noqa: BLE001 - unsupported reasoning fails closed
            supports_reasoning = False

    return CapabilityProbeResult(
        supports_chat_completion=True,
        supports_streaming=True,
        supports_structured_json=supports_structured_json,
        supports_tools=supports_tools,
        supports_vision=supports_vision,
        supports_reasoning=supports_reasoning,
        context_window=context_window,
        latency_ms=max(0, (perf_counter_ns() - started) // 1_000_000),
        error_code=None,
    )
