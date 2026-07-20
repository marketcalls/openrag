from pathlib import Path
from uuid import uuid4

import pytest

import openrag
from openrag.core.errors import ConflictError
from openrag.modules.models.models import Model
from openrag.modules.orchestration.model_gateway import (
    ModelRuntime,
    build_model_runtime,
)


def model(**overrides: object) -> Model:
    values: dict[str, object] = {
        "id": uuid4(),
        "litellm_model_name": "gpt-5-mini",
        "display_name": "GPT-5 Mini",
        "provider_kind": "openai",
        "base_url": None,
        "enabled": True,
    }
    values.update(overrides)
    return Model(**values)


def test_openai_runtime_is_request_scoped_and_secret_safe() -> None:
    runtime = build_model_runtime(
        model(),
        api_key="sk-sensitive-value",
        environment="production",
        max_output_tokens=2_048,
    )

    assert runtime.litellm_model == "openai/gpt-5-mini"
    assert runtime.api_key == "sk-sensitive-value"
    assert runtime.api_base is None
    assert "sk-sensitive-value" not in repr(runtime)
    assert runtime.max_output_tokens == 2_048


def test_existing_litellm_provider_prefix_is_not_duplicated() -> None:
    runtime = build_model_runtime(
        model(litellm_model_name="openai/gpt-5-mini"),
        api_key="secret",
        environment="production",
        max_output_tokens=1_024,
    )

    assert runtime.litellm_model == "openai/gpt-5-mini"


def test_openai_requires_a_stored_credential() -> None:
    with pytest.raises(ConflictError, match="credential"):
        build_model_runtime(
            model(),
            api_key=None,
            environment="production",
            max_output_tokens=1_024,
        )


def test_ollama_runtime_needs_no_key_and_uses_configured_base_url() -> None:
    runtime = build_model_runtime(
        model(
            provider_kind="ollama",
            litellm_model_name="llama3.3",
            base_url="http://ollama:11434",
        ),
        api_key=None,
        environment="dev",
        max_output_tokens=1_024,
    )

    assert runtime.litellm_model == "ollama/llama3.3"
    assert runtime.api_base == "http://ollama:11434"
    assert runtime.api_key is None


@pytest.mark.parametrize(
    "base_url",
    [
        "http://provider.example/v1",
        "ftp://provider.example/v1",
        "https://user:password@provider.example/v1",
        "https:///missing-host",
        "https://provider.example/v1?secret=1",
        "https://provider.example/v1#fragment",
    ],
)
def test_production_provider_url_policy_fails_closed(base_url: str) -> None:
    with pytest.raises(ConflictError, match="base URL"):
        build_model_runtime(
            model(
                provider_kind="openai_compatible",
                base_url=base_url,
            ),
            api_key="secret",
            environment="production",
            max_output_tokens=1_024,
        )


def test_model_runtime_disallows_invalid_output_bounds() -> None:
    with pytest.raises(ValueError, match="max_output_tokens"):
        ModelRuntime(
            litellm_model="openai/gpt-5-mini",
            api_key="secret",
            api_base=None,
            max_output_tokens=0,
        )


def test_provider_secret_decryption_has_only_sanctioned_callers() -> None:
    src_root = Path(openrag.__file__).parent
    allowed = {
        src_root / "modules" / "secrets" / "service.py",
        src_root / "modules" / "orchestration" / "model_gateway.py",
        src_root / "modules" / "embeddings" / "runtime.py",
    }
    offenders = [
        str(path)
        for path in src_root.rglob("*.py")
        if "_get_secret_decrypted" in path.read_text(encoding="utf-8")
        and path not in allowed
    ]
    assert offenders == []
