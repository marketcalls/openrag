import hashlib
import math
import re
from functools import lru_cache
from typing import Any, Protocol

import httpx
from qdrant_client import models

from openrag.core.config import get_settings

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class DenseEmbedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


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
