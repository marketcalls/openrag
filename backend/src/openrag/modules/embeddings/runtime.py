"""Credential-free profile identities resolved into bounded embedding clients."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.config import Settings
from openrag.core.errors import ConflictError
from openrag.modules.documents.profiles import active_ingestion_profiles
from openrag.modules.embeddings.models import EmbeddingDeployment, EmbeddingProfile
from openrag.modules.orchestration.model_gateway import validate_provider_base_url
from openrag.modules.retrieval.embeddings import (
    DenseEmbedder,
    HashDenseEmbedder,
    LiteLLMDenseEmbedder,
    LiteLLMEmbeddingClient,
    TeiDenseEmbedder,
)
from openrag.modules.secrets import service as secrets_service
from openrag.modules.secrets.models import Secret


class RuntimeProfile(Protocol):
    @property
    def provider_kind(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    @property
    def base_url(self) -> str | None: ...

    @property
    def dimension(self) -> int: ...

    @property
    def batch_size(self) -> int: ...

    @property
    def config_digest(self) -> str: ...

    @property
    def enabled(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class EmbeddingRuntime:
    embedder: DenseEmbedder
    dimension: int
    profile_version: str


@dataclass(frozen=True, slots=True)
class _ConfiguredProfile:
    provider_kind: str
    model_name: str
    base_url: str | None
    dimension: int
    batch_size: int
    config_digest: str
    enabled: bool = True


def build_profile_runtime(
    profile: RuntimeProfile,
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    api_key: str | None = None,
    client: LiteLLMEmbeddingClient | None = None,
) -> EmbeddingRuntime:
    """Build only platform-managed clients; profile rows never contain secrets."""

    if not profile.enabled:
        raise ConflictError("disabled embedding profile cannot run")
    if profile.provider_kind == "litellm":
        api_base = None
        if profile.base_url is not None:
            api_base = validate_provider_base_url(
                profile.base_url,
                environment=settings.environment,
            )
        if not profile.model_name.startswith("ollama/") and api_key is None:
            raise ConflictError("embedding model credential is not configured")
        embedder: DenseEmbedder = LiteLLMDenseEmbedder(
            api_key=api_key,
            api_base=api_base,
            model=profile.model_name,
            dimension=profile.dimension,
            batch_size=profile.batch_size,
            client=client,
        )
    elif profile.provider_kind == "tei":
        embedder = TeiDenseEmbedder(
            settings.tei_url,
            batch_size=profile.batch_size,
            transport=transport,
        )
    elif profile.provider_kind == "hash":
        if settings.environment not in {"dev", "test"}:
            raise ConflictError(
                "hash embeddings are restricted to platform development"
            )
        embedder = HashDenseEmbedder(dim=profile.dimension)
    else:
        raise ConflictError("embedding provider is not supported")

    return EmbeddingRuntime(
        embedder=embedder,
        dimension=profile.dimension,
        profile_version=f"embedding/v1/{profile.config_digest}",
    )


def build_configured_runtime(settings: Settings) -> EmbeddingRuntime:
    """Compatibility runtime for the pre-deployment configured generation."""

    version = active_ingestion_profiles(settings).embedding_profile_version
    digest = version.removeprefix("embedding/v1/")
    return build_profile_runtime(
        _ConfiguredProfile(
            provider_kind=settings.embedding_backend,
            model_name=settings.embedding_model_id,
            base_url=None,
            dimension=settings.embedding_dim,
            batch_size=32,
            config_digest=digest,
        ),
        settings,
    )


async def resolve_generation_runtime(
    session_factory: async_sessionmaker[AsyncSession],
    generation_id: UUID,
    settings: Settings,
) -> EmbeddingRuntime:
    """Resolve a generation to its immutable profile without exposing secrets."""

    async with session_factory() as session:
        profile = await session.scalar(
            select(EmbeddingProfile)
            .join(
                EmbeddingDeployment,
                EmbeddingDeployment.profile_id == EmbeddingProfile.id,
            )
            .where(
                EmbeddingDeployment.generation_id == generation_id,
                EmbeddingDeployment.status.in_(("building", "ready", "active")),
            )
        )
        if profile is not None:
            return await resolve_profile_runtime(session, profile, settings)
    if generation_id == settings.authority_generation_id:
        return build_configured_runtime(settings)
    raise ConflictError("embedding generation is not runnable")


async def resolve_profile_runtime(
    session: AsyncSession,
    profile: EmbeddingProfile,
    settings: Settings,
) -> EmbeddingRuntime:
    """Resolve one profile credential for one in-process LiteLLM invocation."""

    api_key = None
    if profile.provider_kind == "litellm":
        secret_name = f"embedding_profile:{profile.id}"
        secret_exists = await session.scalar(
            select(Secret.id).where(Secret.name == secret_name)
        )
        if secret_exists is not None:
            api_key = await secrets_service._get_secret_decrypted(  # noqa: SLF001
                session,
                name=secret_name,
                settings=settings,
            )
    return build_profile_runtime(profile, settings, api_key=api_key)
