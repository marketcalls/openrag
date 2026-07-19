import pytest
from pydantic import ValidationError

from openrag.modules.embeddings.schemas import (
    EmbeddingDeploymentCreate,
    EmbeddingDeploymentOut,
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


def test_embedding_deployment_request_accepts_only_a_profile_identity() -> None:
    deployment = EmbeddingDeploymentCreate.model_validate(
        {"profile_id": "42be9246-631d-4a84-b669-a48953550895"}
    )

    assert str(deployment.profile_id) == "42be9246-631d-4a84-b669-a48953550895"
    with pytest.raises(ValidationError):
        EmbeddingDeploymentCreate.model_validate(
            {
                "profile_id": "42be9246-631d-4a84-b669-a48953550895",
                "generation_id": "566e45b0-051c-4d86-87b3-6a528c7935c2",
            }
        )


def test_embedding_deployment_output_rejects_impossible_progress() -> None:
    values = {
        "id": "4bd2a478-9a6a-4e9d-8385-766a4fbed7ee",
        "profile_id": "42be9246-631d-4a84-b669-a48953550895",
        "generation_id": "566e45b0-051c-4d86-87b3-6a528c7935c2",
        "status": "building",
        "total_versions": 4,
        "completed_versions": 5,
        "failed_versions": 0,
        "scan_complete": False,
        "created_at": "2026-07-20T00:00:00Z",
        "updated_at": "2026-07-20T00:00:00Z",
        "activated_at": None,
        "failure_code": None,
    }

    with pytest.raises(ValidationError):
        EmbeddingDeploymentOut.model_validate(values)
