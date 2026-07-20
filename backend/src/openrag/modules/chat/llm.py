"""Typed streaming client for LiteLLM's OpenAI-compatible endpoint."""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

import httpx

from openrag.core.errors import UpstreamError


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


class LiteLLMStreamer:
    def __init__(
        self,
        *,
        base_url: str,
        master_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._master_key = master_key
        self._transport = transport

    async def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[LLMDelta | LLMUsage]:
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        headers = {"Authorization": f"Bearer {self._master_key}"}

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                transport=self._transport,
                timeout=httpx.Timeout(120.0, connect=10.0),
            ) as client:
                async with client.stream(
                    "POST",
                    "/v1/chat/completions",
                    json=payload,
                    headers=headers,
                ) as response:
                    if response.status_code != 200:
                        await response.aread()
                        raise UpstreamError(
                            "LLM gateway returned "
                            f"{response.status_code}"
                        )

                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line.removeprefix("data: ").strip()
                        if data == "[DONE]":
                            break

                        chunk = json.loads(data)
                        usage = chunk.get("usage")
                        if usage:
                            yield LLMUsage(
                                prompt_tokens=int(
                                    usage.get("prompt_tokens", 0)
                                ),
                                completion_tokens=int(
                                    usage.get("completion_tokens", 0)
                                ),
                                estimated_cost_microusd=max(
                                    0,
                                    round(
                                        float(
                                            usage.get("response_cost")
                                            or usage.get("cost")
                                            or 0
                                        )
                                        * 1_000_000
                                    ),
                                ),
                            )
                            continue

                        choices = chunk.get("choices") or []
                        if choices:
                            delta = (
                                choices[0]
                                .get("delta", {})
                                .get("content")
                            )
                            if delta:
                                yield LLMDelta(text=delta)
        except httpx.HTTPError as exc:
            raise UpstreamError("LLM gateway unreachable") from exc
