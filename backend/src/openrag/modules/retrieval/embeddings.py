import hashlib
import math
import re
from functools import lru_cache
from typing import Any, Protocol

import httpx
from qdrant_client import models

from openrag.core.config import get_settings
from openrag.core.errors import UpstreamError

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class DenseEmbedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class LiteLLMEmbeddingClient(Protocol):
    async def aembedding(self, **kwargs: object) -> object: ...


class TeiDenseEmbedder:
    def __init__(
        self,
        base_url: str,
        batch_size: int = 32,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._batch_size = batch_size
        self._transport = transport

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=60.0,
            transport=self._transport,
        ) as client:
            for start in range(0, len(texts), self._batch_size):
                batch = texts[start : start + self._batch_size]
                response = await client.post(
                    "/embed",
                    json={"inputs": batch, "truncate": True},
                )
                response.raise_for_status()
                vectors.extend(response.json())
        return vectors


class LiteLLMDenseEmbedder:
    """Validated, request-scoped embedding execution through LiteLLM's SDK."""

    def __init__(
        self,
        *,
        api_key: str | None,
        api_base: str | None,
        model: str,
        dimension: int,
        batch_size: int = 32,
        client: LiteLLMEmbeddingClient | None = None,
    ) -> None:
        if dimension < 1 or batch_size < 1:
            raise ValueError("dimension and batch size must be positive")
        if client is None:
            import litellm

            client = litellm
        self._api_key = api_key
        self._api_base = api_base
        self._model = model
        self._dimension = dimension
        self._batch_size = batch_size
        self._client = client

    def _parse_vectors(
        self,
        payload: object,
        *,
        expected_count: int,
    ) -> list[list[float]]:
        if not isinstance(payload, dict):
            raise UpstreamError("invalid embedding response")
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != expected_count:
            raise UpstreamError("invalid embedding response")

        ordered: list[list[float] | None] = [None] * expected_count
        for item in data:
            if not isinstance(item, dict):
                raise UpstreamError("invalid embedding response")
            index = item.get("index")
            raw_vector = item.get("embedding")
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or not 0 <= index < expected_count
                or ordered[index] is not None
                or not isinstance(raw_vector, list)
                or len(raw_vector) != self._dimension
            ):
                raise UpstreamError("invalid embedding response")

            vector: list[float] = []
            for value in raw_vector:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise UpstreamError("invalid embedding response")
                number = float(value)
                if not math.isfinite(number):
                    raise UpstreamError("invalid embedding response")
                vector.append(number)
            ordered[index] = vector

        if any(vector is None for vector in ordered):
            raise UpstreamError("invalid embedding response")
        return [vector for vector in ordered if vector is not None]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            request: dict[str, object] = {
                "model": self._model,
                "input": batch,
                "timeout": 120.0,
            }
            if self._api_key is not None:
                request["api_key"] = self._api_key
            if self._api_base is not None:
                request["api_base"] = self._api_base
            try:
                response = await self._client.aembedding(**request)
            except Exception as exc:  # noqa: BLE001 - provider exception taxonomy varies
                raise UpstreamError("embedding model execution failed") from exc
            payload = response
            model_dump = getattr(response, "model_dump", None)
            if callable(model_dump):
                payload = model_dump()
            vectors.extend(self._parse_vectors(payload, expected_count=len(batch)))
        return vectors


class HashDenseEmbedder:
    """Deterministic, normalized lexical embedding for tests and local use."""

    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.sha256(token.encode()).hexdigest()
            vector[int(digest, 16) % self._dim] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


@lru_cache
def get_dense_embedder() -> DenseEmbedder:
    settings = get_settings()
    if settings.embedding_backend == "hash":
        return HashDenseEmbedder(dim=settings.embedding_dim)
    return TeiDenseEmbedder(settings.tei_url)


@lru_cache
def _bm25_model() -> Any:
    from fastembed import SparseTextEmbedding

    return SparseTextEmbedding("Qdrant/bm25")


def embed_sparse(texts: list[str]) -> list[models.SparseVector]:
    return [
        models.SparseVector(
            indices=embedding.indices.tolist(),
            values=embedding.values.tolist(),
        )
        for embedding in _bm25_model().embed(texts)
    ]
