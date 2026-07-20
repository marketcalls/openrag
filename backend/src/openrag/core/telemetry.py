"""Correlation context and bounded recursive redaction for telemetry metadata."""

import math
import re
from collections.abc import Mapping, Sequence
from contextvars import ContextVar, Token
from datetime import date, datetime
from enum import Enum
from itertools import islice
from pathlib import Path
from typing import TypeGuard
from uuid import UUID, uuid4

type TraceToken = Token[str | None]
type JsonSafe = None | bool | int | float | str | dict[str, JsonSafe] | list[JsonSafe]

_TRACE_RE = re.compile(r"^[0-9a-f]{32}$")
_TRACE_ID: ContextVar[str | None] = ContextVar("openrag_trace_id", default=None)
_MAX_DEPTH = 6
_MAX_ITEMS = 50
_MAX_STRING = 512
_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set_cookie",
        "password",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "master_key",
        "private_key",
        "credential",
        "credentials",
        "prompt",
        "response",
        "request_body",
        "response_body",
        "body",
        "query",
        "retrieved_text",
        "document_text",
        "chunk_text",
        "content",
        "memory",
        "filename",
        "file_name",
        "tool_arguments",
        "tool_result",
        "reasoning",
        "chain_of_thought",
        "exception_message",
        "stacktrace",
        "exc_info",
    }
)


def valid_trace_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and _TRACE_RE.fullmatch(value) is not None


def new_trace_id(inbound: object = None) -> str:
    return inbound if valid_trace_id(inbound) else uuid4().hex


def set_trace_id(trace_id: str) -> TraceToken:
    if not valid_trace_id(trace_id):
        raise ValueError("trace_id_invalid")
    return _TRACE_ID.set(trace_id)


def reset_trace_id(token: TraceToken) -> None:
    _TRACE_ID.reset(token)


def current_trace_id() -> str:
    trace_id = _TRACE_ID.get()
    if trace_id is None:
        trace_id = new_trace_id()
        _TRACE_ID.set(trace_id)
    return trace_id


def _sensitive_key(key: str) -> bool:
    normalized = key.strip().casefold().replace("-", "_")
    return (
        normalized in _SENSITIVE_KEYS
        or normalized.endswith("_password")
        or normalized.endswith("_secret")
        or normalized.endswith("_token")
        or normalized.endswith("_api_key")
        or normalized.endswith("_credential")
    )


def _bounded_string(value: str) -> str:
    if len(value) <= _MAX_STRING:
        return value
    return f"{value[:_MAX_STRING]}[TRUNCATED]"


def safe_log_fields(value: object, *, _depth: int = 0) -> JsonSafe:
    """Convert structured values without invoking untrusted ``repr`` methods."""

    if _depth > _MAX_DEPTH:
        return "[MAX_DEPTH]"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "[NON_FINITE]"
    if isinstance(value, str):
        return _bounded_string(value)
    if isinstance(value, bytes):
        return f"[BINARY:{len(value)}]"
    if isinstance(value, BaseException):
        return {"exception_type": type(value).__name__}
    if isinstance(value, (UUID, datetime, date, Path, Enum)):
        return _bounded_string(str(value))
    if isinstance(value, Mapping):
        result: dict[str, JsonSafe] = {}
        mapping_items = list(islice(value.items(), _MAX_ITEMS))
        for raw_key, nested in mapping_items:
            key = _bounded_string(raw_key if isinstance(raw_key, str) else type(raw_key).__name__)
            result[key] = (
                "[REDACTED]" if _sensitive_key(key) else safe_log_fields(nested, _depth=_depth + 1)
            )
        if len(value) > _MAX_ITEMS:
            result["__truncated__"] = len(value) - _MAX_ITEMS
        return result
    if isinstance(value, Sequence):
        sequence_items: list[JsonSafe] = [
            safe_log_fields(item, _depth=_depth + 1) for item in value[:_MAX_ITEMS]
        ]
        if len(value) > _MAX_ITEMS:
            sequence_items.append("[TRUNCATED]")
        return sequence_items
    return {"type": type(value).__name__}
