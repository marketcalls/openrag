import json
import math

import httpx

from openrag.modules.retrieval.embeddings import (
    HashDenseEmbedder,
    TeiDenseEmbedder,
    embed_sparse,
)


def cosine(left: list[float], right: list[float]) -> float:
    return sum(x * y for x, y in zip(left, right, strict=True))


async def test_hash_embedder_is_deterministic_and_normalized() -> None:
    embedder = HashDenseEmbedder(dim=64)

    [first] = await embedder.embed(["the flux capacitor hums"])
    [second] = await embedder.embed(["the flux capacitor hums"])

    assert first == second
    assert len(first) == 64
    assert math.isclose(sum(value * value for value in first), 1.0, rel_tol=1e-6)


async def test_hash_embedder_overlap_beats_disjoint_text() -> None:
    embedder = HashDenseEmbedder(dim=256)
    query, hit, miss = await embedder.embed(
        [
            "flux capacitor invoice",
            "invoice 0231 for the flux capacitor",
            "quarterly kumquat report",
        ]
    )

    assert cosine(query, hit) > cosine(query, miss)


async def test_tei_embedder_batches_and_parses() -> None:
    calls: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        inputs: list[str] = json.loads(request.content)["inputs"]
        calls.append(inputs)
        return httpx.Response(200, json=[[0.1, 0.2]] * len(inputs))

    embedder = TeiDenseEmbedder(
        "http://tei",
        batch_size=2,
        transport=httpx.MockTransport(handler),
    )

    vectors = await embedder.embed(["a", "b", "c"])

    assert vectors == [[0.1, 0.2]] * 3
    assert [len(call) for call in calls] == [2, 1]


def test_sparse_bm25_hits_shared_terms() -> None:
    document, query = embed_sparse(["invoice 0231 total due", "invoice 0231"])

    assert set(query.indices) & set(document.indices)
    assert all(value > 0 for value in document.values)
