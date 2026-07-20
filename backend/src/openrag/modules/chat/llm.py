"""Provider-neutral streaming contracts for in-process model adapters."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMDelta:
    text: str


@dataclass(frozen=True)
class LLMUsage:
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_microusd: int = 0


class LLMStreamer(Protocol):
    def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[LLMDelta | LLMUsage]: ...
