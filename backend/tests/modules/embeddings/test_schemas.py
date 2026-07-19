import pytest
from pydantic import ValidationError

from openrag.modules.embeddings.schemas import (
    EmbeddingProfileCreate,
    embedding_config_digest,
)


def profile(**overrides: object) -> EmbeddingProfileCreate:
    values: dict[str, object] = {
        "name": "Production BGE",
        "provider_kind": "litellm",
        "model_name": "huggingface/BAAI/bge-m3",
        "dimension": 1024,
        "max_input_tokens": 8192,
        "batch_size": 32,
    }
    values.update(overrides)
    return EmbeddingProfileCreate.model_validate(values)


def test_embedding_profile_normalizes_and_has_stable_content_identity() -> None:
    first = profile(name="  Production   BGE  ")
    renamed = profile(name="Renamed")
    changed = profile(dimension=768)

    assert first.name == "Production BGE"
    assert embedding_config_digest(first) == embedding_config_digest(renamed)
    assert embedding_config_digest(first) != embedding_config_digest(changed)
    assert len(embedding_config_digest(first)) == 64


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_kind", "openai"),
        ("dimension", 0),
        ("dimension", 32769),
        ("max_input_tokens", 0),
        ("batch_size", 0),
        ("batch_size", 1025),
        ("name", "   "),
        ("model_name", "   "),
    ],
)
def test_embedding_profile_rejects_unsafe_or_unbounded_values(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        profile(**{field: value})
