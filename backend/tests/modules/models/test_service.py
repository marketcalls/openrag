from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import Settings
from openrag.core.errors import (
    ConflictError,
    NotFoundError,
    SecretsError,
)
from openrag.modules.audit.models import AuditEvent
from openrag.modules.auth.models import User
from openrag.modules.models.models import ModelProbe
from openrag.modules.models.service import (
    create_model,
    delete_model,
    list_enabled_models,
    list_models,
    request_model_probe,
    resolve_model,
    to_model_out,
    update_model,
)
from openrag.modules.models.utility import resolve_utility_model
from openrag.modules.secrets.crypto import ensure_kek
from openrag.modules.secrets.models import Secret
from openrag.modules.tenancy.authorization import AuthorizationSnapshot
from openrag.modules.tenancy.context import TenantContext


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    kek = tmp_path / "kek"
    ensure_kek(str(kek))
    return Settings(_env_file=None, kek_file=str(kek))


def super_ctx(user: User) -> TenantContext:
    return TenantContext(
        user_id=user.id,
        org_id=user.org_id,
        authorization=AuthorizationSnapshot(
            user_id=user.id,
            org_id=user.org_id,
            is_platform_superadmin=True,
            org_permissions=frozenset(),
            workspace_permissions={},
            workspace_ids=frozenset(),
        ),
    )


async def test_create_stores_key_as_secret(
    session: AsyncSession,
    seeded_user: User,
    settings: Settings,
) -> None:
    ctx = super_ctx(seeded_user)
    model = await create_model(
        session,
        ctx,
        litellm_model_name="gpt-4o-mini",
        display_name="GPT-4o mini",
        provider_kind="openai",
        base_url=None,
        api_key="sk-live-xyz",
        settings=settings,
    )

    secret = (
        await session.execute(
            select(Secret).where(Secret.name == f"model:{model.id}")
        )
    ).scalar_one()
    assert b"sk-live-xyz" not in secret.ciphertext
    assert not hasattr(model, "sync_status")
    assert model.probe_status == "pending"
    assert model.probe_revision == 1
    assert model.supports_chat_completion is False
    assert model.supports_streaming is False
    assert model.supports_tools is False
    assert model.supports_vision is False
    probe = (
        await session.execute(select(ModelProbe).where(ModelProbe.model_id == model.id))
    ).scalar_one()
    assert probe.revision == 1
    assert probe.status == "queued"
    assert probe.requested_by == seeded_user.id
    actions = [
        event.action
        for event in (await session.execute(select(AuditEvent))).scalars()
    ]
    assert "model.created" in actions
    assert "secret.written" in actions

    [output] = await to_model_out(session, [model])
    assert output.key_fingerprint == secret.fingerprint
    assert not hasattr(output, "api_key")
    assert output.probe_status == "pending"


async def test_manual_probe_request_is_revisioned_and_invalidates_old_capabilities(
    session: AsyncSession,
    seeded_user: User,
    settings: Settings,
) -> None:
    ctx = super_ctx(seeded_user)
    model = await create_model(
        session,
        ctx,
        litellm_model_name="gpt-4o-mini",
        display_name="GPT-4o mini",
        provider_kind="openai",
        base_url=None,
        api_key="sk-write-only",
        settings=settings,
    )
    model.supports_chat_completion = True
    model.supports_streaming = True
    model.supports_tools = True
    model.probe_status = "passed"
    await session.commit()

    probe = await request_model_probe(session, ctx, model.id)

    await session.refresh(model)
    assert probe.revision == 2
    assert model.probe_revision == 2
    assert model.probe_status == "pending"
    assert model.supports_chat_completion is False
    assert model.supports_streaming is False
    assert model.supports_tools is False


async def test_missing_kek_does_not_leave_a_partial_model(
    session: AsyncSession,
    seeded_user: User,
    tmp_path: Path,
) -> None:
    missing_kek = Settings(
        _env_file=None,
        kek_file=str(tmp_path / "missing" / "openrag_kek"),
    )

    with pytest.raises(SecretsError, match="KEK file missing"):
        await create_model(
            session,
            super_ctx(seeded_user),
            litellm_model_name="gpt-5.6-luna",
            display_name="GPT-5.6 Luna",
            provider_kind="openai",
            base_url=None,
            api_key="sk-write-only",
            settings=missing_kek,
        )

    assert await list_models(session) == []


async def test_update_and_disable(
    session: AsyncSession,
    seeded_user: User,
    settings: Settings,
) -> None:
    ctx = super_ctx(seeded_user)
    model = await create_model(
        session,
        ctx,
        litellm_model_name="llama3",
        display_name="Llama 3",
        provider_kind="ollama",
        base_url="http://ollama:11434",
        api_key=None,
        settings=settings,
    )

    updated = await update_model(
        session,
        ctx,
        model.id,
        display_name="Llama 3 8B",
        base_url=None,
        enabled=False,
        api_key=None,
        settings=settings,
    )
    assert updated.display_name == "Llama 3 8B"
    assert updated.base_url == "http://ollama:11434"
    assert updated.enabled is False
    assert await list_enabled_models(session) == []
    assert len(await list_models(session)) == 1


async def test_utility_model_is_single_measured_and_cleared_when_disabled(
    session: AsyncSession,
    seeded_user: User,
    settings: Settings,
) -> None:
    ctx = super_ctx(seeded_user)
    first = await create_model(
        session,
        ctx,
        litellm_model_name="utility-one",
        display_name="Utility one",
        provider_kind="ollama",
        base_url="http://ollama:11434",
        api_key=None,
        settings=settings,
    )
    second = await create_model(
        session,
        ctx,
        litellm_model_name="utility-two",
        display_name="Utility two",
        provider_kind="ollama",
        base_url="http://ollama:11434",
        api_key=None,
        settings=settings,
    )
    for model in (first, second):
        model.probe_status = "passed"
        model.supports_chat_completion = True
        model.supports_streaming = True
    await session.commit()

    await update_model(
        session,
        ctx,
        first.id,
        display_name=None,
        base_url=None,
        enabled=None,
        api_key=None,
        settings=settings,
        is_utility=True,
    )
    assert (await resolve_utility_model(session)).id == first.id  # type: ignore[union-attr]

    await update_model(
        session,
        ctx,
        second.id,
        display_name=None,
        base_url=None,
        enabled=None,
        api_key=None,
        settings=settings,
        is_utility=True,
    )
    await session.refresh(first)
    assert first.is_utility is False
    assert (await resolve_utility_model(session)).id == second.id  # type: ignore[union-attr]

    await update_model(
        session,
        ctx,
        second.id,
        display_name=None,
        base_url=None,
        enabled=False,
        api_key=None,
        settings=settings,
    )
    assert await resolve_utility_model(session) is None


async def test_unmeasured_model_cannot_be_designated_as_utility(
    session: AsyncSession,
    seeded_user: User,
    settings: Settings,
) -> None:
    ctx = super_ctx(seeded_user)
    model = await create_model(
        session,
        ctx,
        litellm_model_name="unmeasured-utility",
        display_name="Unmeasured",
        provider_kind="ollama",
        base_url="http://ollama:11434",
        api_key=None,
        settings=settings,
    )

    with pytest.raises(ConflictError, match="measured chat and streaming"):
        await update_model(
            session,
            ctx,
            model.id,
            display_name=None,
            base_url=None,
            enabled=None,
            api_key=None,
            settings=settings,
            is_utility=True,
        )


async def test_changed_model_must_be_reprobed_before_utility_designation(
    session: AsyncSession,
    seeded_user: User,
    settings: Settings,
) -> None:
    ctx = super_ctx(seeded_user)
    model = await create_model(
        session,
        ctx,
        litellm_model_name="changed-utility",
        display_name="Changed utility",
        provider_kind="ollama",
        base_url="http://ollama:11434",
        api_key=None,
        settings=settings,
    )
    model.probe_status = "passed"
    model.supports_chat_completion = True
    model.supports_streaming = True
    await session.commit()

    with pytest.raises(ConflictError, match="re-probe the changed model"):
        await update_model(
            session,
            ctx,
            model.id,
            display_name=None,
            base_url="http://ollama-new:11434",
            enabled=None,
            api_key=None,
            settings=settings,
            is_utility=True,
        )


async def test_delete_removes_model_and_secret(
    session: AsyncSession,
    seeded_user: User,
    settings: Settings,
) -> None:
    ctx = super_ctx(seeded_user)
    model = await create_model(
        session,
        ctx,
        litellm_model_name="gpt-4o",
        display_name="GPT-4o",
        provider_kind="openai",
        base_url=None,
        api_key="sk-1",
        settings=settings,
    )

    await delete_model(session, ctx, model.id)
    assert await list_models(session) == []
    assert (
        await session.execute(
            select(Secret).where(Secret.name == f"model:{model.id}")
        )
    ).scalar_one_or_none() is None
    with pytest.raises(NotFoundError):
        await delete_model(session, ctx, uuid4())


async def test_resolve_model_order(
    session: AsyncSession,
    seeded_user: User,
    settings: Settings,
) -> None:
    ctx = super_ctx(seeded_user)
    default = await create_model(
        session,
        ctx,
        litellm_model_name="llama3",
        display_name="Llama",
        provider_kind="ollama",
        base_url="http://ollama:11434",
        api_key=None,
        settings=settings,
    )
    override = await create_model(
        session,
        ctx,
        litellm_model_name="mistral",
        display_name="Mistral",
        provider_kind="ollama",
        base_url="http://ollama:11434",
        api_key=None,
        settings=settings,
    )
    for model in (default, override):
        model.probe_status = "passed"
        model.supports_chat_completion = True
        model.supports_streaming = True
    await session.commit()

    got = await resolve_model(
        session,
        requested_model_id=override.id,
        default_model_id=default.id,
    )
    assert got.id == override.id

    got = await resolve_model(
        session,
        requested_model_id=None,
        default_model_id=default.id,
    )
    assert got.id == default.id

    with pytest.raises(NotFoundError):
        await resolve_model(
            session,
            requested_model_id=uuid4(),
            default_model_id=default.id,
        )

    await update_model(
        session,
        ctx,
        override.id,
        display_name=None,
        base_url=None,
        enabled=False,
        api_key=None,
        settings=settings,
    )
    with pytest.raises(NotFoundError):
        await resolve_model(
            session,
            requested_model_id=override.id,
            default_model_id=default.id,
        )

    with pytest.raises(ConflictError):
        await resolve_model(
            session,
            requested_model_id=None,
            default_model_id=None,
        )
