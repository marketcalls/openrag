import json
import math

import httpx
import pytest

from openrag.core.errors import UpstreamError
from openrag.modules.retrieval.embeddings import (
    HashDenseEmbedder,
    LiteLLMDenseEmbedder,
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


async def test_litellm_embedder_uses_gateway_and_restores_index_order() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = json.loads(request.content)
        if body["input"] == ["a", "b"]:
            data = [
                {"index": 1, "embedding": [2.0, 0.0]},
                {"index": 0, "embedding": [1.0, 0.0]},
            ]
        else:
            data = [{"index": 0, "embedding": [3.0, 0.0]}]
        return httpx.Response(
            200,
            json={"data": data},
        )

    embedder = LiteLLMDenseEmbedder(
        base_url="http://litellm",
        master_key="gateway-secret",
        model="huggingface/BAAI/bge-m3",
        dimension=2,
        batch_size=2,
        transport=httpx.MockTransport(handler),
    )

    vectors = await embedder.embed(["a", "b", "c"])

    assert vectors == [[1.0, 0.0], [2.0, 0.0], [3.0, 0.0]]
    assert [request.url.path for request in calls] == [
        "/v1/embeddings",
        "/v1/embeddings",
    ]
    assert [json.loads(request.content)["input"] for request in calls] == [
        ["a", "b"],
        ["c"],
    ]
    assert all(
        request.headers["authorization"] == "Bearer gateway-secret"
        for request in calls
    )
    assert all(
        json.loads(request.content)["model"] == "huggingface/BAAI/bge-m3"
        for request in calls
    )


@pytest.mark.parametrize(
    "response_json",
    [
        {"data": []},
        {"data": [{"index": 0, "embedding": [1.0]}]},
        {"data": [{"index": 0, "embedding": ["nan", 0.0]}]},
    ],
)
async def test_litellm_embedder_rejects_malformed_vectors(
    response_json: dict[str, object],
) -> None:
    embedder = LiteLLMDenseEmbedder(
        base_url="http://litellm",
        master_key="gateway-secret",
        model="embedding-model",
        dimension=2,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=response_json)
        ),
    )

    with pytest.raises(UpstreamError, match="invalid embedding response"):
        await embedder.embed(["document"])


async def test_litellm_embedder_rejects_non_finite_vectors() -> None:
    embedder = LiteLLMDenseEmbedder(
        base_url="http://litellm",
        master_key="gateway-secret",
        model="embedding-model",
        dimension=2,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                content=b'{"data":[{"index":0,"embedding":[NaN,0.0]}]}',
                headers={"content-type": "application/json"},
            )
        ),
    )

    with pytest.raises(UpstreamError, match="invalid embedding response"):
        await embedder.embed(["document"])


async def test_litellm_embedder_sanitizes_gateway_failure() -> None:
    embedder = LiteLLMDenseEmbedder(
        base_url="http://litellm",
        master_key="gateway-secret",
        model="embedding-model",
        dimension=2,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                401,
                json={"error": "gateway-secret must never escape"},
            )
        ),
    )

    with pytest.raises(UpstreamError) as caught:
        await embedder.embed(["document"])

    assert caught.value.detail == "embedding gateway returned 401"
    assert "gateway-secret" not in str(caught.value)


def test_sparse_bm25_hits_shared_terms() -> None:
    document, query = embed_sparse(["invoice 0231 total due", "invoice 0231"])

    assert set(query.indices) & set(document.indices)
    assert all(value > 0 for value in document.values)
