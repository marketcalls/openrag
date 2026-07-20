"""Secret-safe request-scoped configuration for in-process LiteLLM models."""

from dataclasses import dataclass, field

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import Settings
from openrag.core.errors import ConflictError
from openrag.modules.models.models import Model
from openrag.modules.models.reasoning import REASONING_EFFORTS, ReasoningEffort
from openrag.modules.secrets import service as secrets_service
from openrag.modules.secrets.models import Secret


@dataclass(frozen=True, slots=True)
class ModelRuntime:
    litellm_model: str
    api_key: str | None = field(repr=False, compare=False)
    api_base: str | None
    max_output_tokens: int
    reasoning_effort: ReasoningEffort = "off"

    def __post_init__(self) -> None:
        if not 1 <= len(self.litellm_model) <= 200:
            raise ValueError("litellm_model must contain between 1 and 200 characters")
        if not 1 <= self.max_output_tokens <= 32_768:
            raise ValueError("max_output_tokens must be between 1 and 32768")
        if self.reasoning_effort not in REASONING_EFFORTS:
            raise ValueError("reasoning_effort is invalid")


def validate_provider_base_url(value: str, *, environment: str) -> str:
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL as exc:
        raise ConflictError("model base URL is invalid") from exc
    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.userinfo
        or url.query
        or url.fragment
        or (environment not in {"dev", "test"} and url.scheme != "https")
    ):
        raise ConflictError("model base URL is not allowed")
    return str(url).rstrip("/")


def _model_name(provider_kind: str, configured_name: str) -> str:
    prefixes = {
        "openai": "openai",
        "openai_compatible": "openai",
        "ollama": "ollama",
    }
    prefix = prefixes.get(provider_kind)
    if prefix is None:
        raise ConflictError("model provider is not supported")
    if configured_name.startswith(f"{prefix}/"):
        return configured_name
    return f"{prefix}/{configured_name}"


def build_model_runtime(
    model: Model,
    *,
    api_key: str | None,
    environment: str,
    max_output_tokens: int,
    reasoning_effort: ReasoningEffort = "off",
) -> ModelRuntime:
    """Build immutable provider arguments without mutating process globals."""

    if model.provider_kind == "openai" and not api_key:
        raise ConflictError("model credential is not configured")
    if model.provider_kind in {"openai_compatible", "ollama"}:
        if not model.base_url:
            raise ConflictError("model base URL is required")
        api_base = validate_provider_base_url(
            model.base_url,
            environment=environment,
        )
    else:
        api_base = None
    return ModelRuntime(
        litellm_model=_model_name(
            model.provider_kind,
            model.litellm_model_name,
        ),
        api_key=api_key,
        api_base=api_base,
        max_output_tokens=max_output_tokens,
        reasoning_effort=reasoning_effort,
    )


async def resolve_model_runtime(
    session: AsyncSession,
    model: Model,
    settings: Settings,
    *,
    reasoning_effort: ReasoningEffort = "off",
) -> ModelRuntime:
    """Resolve the optional encrypted key only for this model invocation."""

    secret_name = f"model:{model.id}"
    secret_exists = await session.scalar(
        select(Secret.id).where(Secret.name == secret_name)
    )
    api_key = None
    if secret_exists is not None:
        api_key = await secrets_service._get_secret_decrypted(  # noqa: SLF001
            session,
            name=secret_name,
            settings=settings,
        )
    return build_model_runtime(
        model,
        api_key=api_key,
        environment=settings.environment,
        max_output_tokens=settings.chat_max_output_tokens,
        reasoning_effort=reasoning_effort,
    )
