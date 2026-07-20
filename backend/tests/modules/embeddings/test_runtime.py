from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from openrag.core.config import Settings
from openrag.core.errors import ConflictError
from openrag.modules.embeddings.runtime import (
    build_configured_runtime,
    build_profile_runtime,
)


async def test_litellm_profile_runtime_uses_request_scoped_library() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def aembedding(self, **kwargs: object) -> object:
            self.calls.append(kwargs)
            return {"data": [{"index": 0, "embedding": [0.25, 0.75]}]}

    client = Client()

    profile = SimpleNamespace(
        id=uuid4(),
        provider_kind="litellm",
        model_name="openai/text-embedding-3-small",
        base_url="https://embeddings.example/v1",
        dimension=2,
        batch_size=8,
        config_digest="a" * 64,
        enabled=True,
    )
    runtime = build_profile_runtime(
        profile,
        Settings(environment="production"),
        api_key="provider-secret",
        client=client,
    )

    assert runtime.dimension == 2
    assert runtime.profile_version == f"embedding/v1/{'a' * 64}"
    assert await runtime.embedder.embed(["policy"]) == [[0.25, 0.75]]
    assert client.calls[0]["api_key"] == "provider-secret"
    assert client.calls[0]["api_base"] == "https://embeddings.example/v1"


async def test_tei_profile_runtime_uses_platform_managed_endpoint() -> None:
    profile = SimpleNamespace(
        id=uuid4(),
        provider_kind="tei",
        model_name="BAAI/bge-m3",
        base_url=None,
        dimension=2,
        batch_size=4,
        config_digest="b" * 64,
        enabled=True,
    )
    runtime = build_profile_runtime(
        profile,
        Settings(environment="production", tei_url="http://tei"),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=[[0.1, 0.2]])
        ),
    )

    assert await runtime.embedder.embed(["policy"]) == [[0.1, 0.2]]


def test_runtime_rejects_disabled_and_production_hash_profiles() -> None:
    base = {
        "id": uuid4(),
        "model_name": "hash-v1",
        "base_url": None,
        "dimension": 2,
        "batch_size": 4,
        "config_digest": "c" * 64,
    }

    with pytest.raises(ConflictError, match="disabled"):
        build_profile_runtime(
            SimpleNamespace(**base, provider_kind="litellm", enabled=False),
            Settings(environment="production"),
        )
    with pytest.raises(ConflictError, match="development"):
        build_profile_runtime(
            SimpleNamespace(**base, provider_kind="hash", enabled=True),
            Settings(environment="production"),
        )


def test_configured_runtime_preserves_legacy_generation_identity() -> None:
    settings = Settings(
        environment="test",
        embedding_backend="hash",
        embedding_model_id="hash-v1",
        embedding_dim=3,
    )

    runtime = build_configured_runtime(settings)

    assert runtime.dimension == 3
    assert runtime.profile_version.startswith("embedding/v1/")
    assert len(runtime.profile_version) == len("embedding/v1/") + 64
