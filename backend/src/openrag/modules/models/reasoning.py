"""Validated public reasoning controls without exposing private reasoning traces."""

from typing import Literal

from openrag.core.errors import InvalidRequestError

ReasoningEffort = Literal["off", "low", "medium", "high"]
REASONING_EFFORTS: tuple[ReasoningEffort, ...] = (
    "off",
    "low",
    "medium",
    "high",
)


def resolve_reasoning_effort(
    *,
    supports_reasoning: bool,
    default_effort: str,
    requested_effort: ReasoningEffort | None,
) -> ReasoningEffort:
    """Resolve one request's effort and reject invalid capabilities early."""

    chosen = requested_effort if requested_effort is not None else default_effort
    if chosen not in REASONING_EFFORTS:
        raise InvalidRequestError("model reasoning configuration is invalid")
    effort = chosen
    if effort != "off" and not supports_reasoning:
        raise InvalidRequestError("model does not support reasoning effort")
    return effort
