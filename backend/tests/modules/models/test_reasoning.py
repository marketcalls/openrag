import pytest
from pydantic import ValidationError

from openrag.core.errors import InvalidRequestError
from openrag.modules.models.reasoning import resolve_reasoning_effort
from openrag.modules.models.schemas import ModelCreate, ModelPatch


def test_model_capabilities_cannot_be_declared_before_live_probe() -> None:
    with pytest.raises(ValidationError):
        ModelCreate(
            litellm_model_name="gpt-5-mini",
            display_name="GPT-5 mini",
            provider_kind="openai",
            supports_reasoning=True,
            default_reasoning_effort="medium",
        )
    with pytest.raises(ValidationError):
        ModelCreate(
            litellm_model_name="gpt-4o-mini",
            display_name="GPT-4o mini",
            provider_kind="openai",
            default_reasoning_effort="high",
        )
    patch = ModelPatch(default_reasoning_effort="medium")
    assert patch.default_reasoning_effort == "medium"
    with pytest.raises(ValidationError):
        ModelPatch(
            supports_reasoning=False,
            default_reasoning_effort="high",
        )


@pytest.mark.parametrize("effort", ["low", "medium", "high"])
def test_supported_model_resolves_explicit_reasoning_effort(effort: str) -> None:
    assert (
        resolve_reasoning_effort(
            supports_reasoning=True,
            default_effort="medium",
            requested_effort=effort,
        )
        == effort
    )


def test_model_default_is_used_when_request_omits_reasoning_effort() -> None:
    assert (
        resolve_reasoning_effort(
            supports_reasoning=True,
            default_effort="medium",
            requested_effort=None,
        )
        == "medium"
    )


def test_unsupported_model_rejects_non_off_reasoning_before_execution() -> None:
    with pytest.raises(
        InvalidRequestError,
        match="does not support reasoning effort",
    ):
        resolve_reasoning_effort(
            supports_reasoning=False,
            default_effort="off",
            requested_effort="high",
        )


def test_off_is_valid_for_every_model() -> None:
    assert (
        resolve_reasoning_effort(
            supports_reasoning=False,
            default_effort="off",
            requested_effort="off",
        )
        == "off"
    )
