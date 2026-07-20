import pytest
from pydantic import ValidationError

from openrag.modules.models.schemas import ModelCreate, ModelPatch


def model_create(**overrides: object) -> ModelCreate:
    values: dict[str, object] = {
        "litellm_model_name": "gpt-5-mini",
        "display_name": "GPT-5 Mini",
        "provider_kind": "openai",
        "api_key": "sk-write-only",
    }
    values.update(overrides)
    return ModelCreate.model_validate(values)


def test_registered_models_default_to_chat_capable() -> None:
    model = model_create()

    assert model.supports_chat_completion is True
    assert model.supports_structured_json is False
    assert model.supports_verifier is False


def test_model_credentials_are_not_exposed_in_representations() -> None:
    created = model_create(api_key="sk-create-secret")
    patched = ModelPatch(api_key="sk-patch-secret")

    assert "sk-create-secret" not in repr(created)
    assert "sk-patch-secret" not in repr(patched)


@pytest.mark.parametrize(
    "overrides",
    [
        {"supports_chat_completion": False, "supports_structured_json": True},
        {"supports_structured_json": False, "supports_verifier": True},
        {"supports_chat_completion": False, "supports_verifier": True},
    ],
)
def test_model_capability_hierarchy_fails_closed(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="capability"):
        model_create(**overrides)


def test_patch_rejects_explicitly_inconsistent_capabilities() -> None:
    with pytest.raises(ValidationError, match="capability"):
        ModelPatch.model_validate(
            {
                "supports_chat_completion": False,
                "supports_structured_json": True,
            }
        )
