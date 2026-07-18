import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import openrag
from openrag.core.config import Settings
from openrag.core.errors import UpstreamError
from openrag.modules.auth.models import User
from openrag.modules.models.service import create_model, list_models, update_model
from openrag.modules.models.sync import sync_models_to_litellm
from openrag.modules.secrets.crypto import ensure_kek
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
        role="superadmin",
        workspace_ids=frozenset(),
    )


class Recorder:
    """Record the LiteLLM HTTP contract at the httpx transport boundary."""

    def __init__(
        self,
        deployed_ids: list[str] | None = None,
        fail: bool = False,
    ) -> None:
        self.deployed_ids = deployed_ids or []
        self.fail = fail
        self.calls: list[tuple[str, str, bytes]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.content))
        if self.fail:
            return httpx.Response(500, json={"error": "boom"})
        if request.url.path == "/v1/model/info":
            data = [
                {"model_info": {"id": deployed_id}}
                for deployed_id in self.deployed_ids
            ]
            return httpx.Response(200, json={"data": data})
        return httpx.Response(200, json={})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


async def seed_two_models(
    session: AsyncSession,
    user: User,
    settings: Settings,
) -> None:
    ctx = super_ctx(user)
    await create_model(
        session,
        ctx,
        litellm_model_name="gpt-4o-mini",
        display_name="GPT",
        provider_kind="openai",
        base_url=None,
        api_key="sk-live-777",
        settings=settings,
    )
    second = await create_model(
        session,
        ctx,
        litellm_model_name="llama3",
        display_name="Llama",
        provider_kind="ollama",
        base_url="http://ollama:11434",
        api_key=None,
        settings=settings,
    )
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


async def test_replay_deletes_then_deploys_enabled_only(
    session: AsyncSession,
    seeded_user: User,
    settings: Settings,
) -> None:
    await seed_two_models(session, seeded_user, settings)
    recorder = Recorder(deployed_ids=["stale-a", "stale-b"])

    count = await sync_models_to_litellm(
        session,
        settings,
        transport=recorder.transport,
    )

    assert count == 1
    assert [(method, path) for method, path, _ in recorder.calls] == [
        ("GET", "/v1/model/info"),
        ("POST", "/model/delete"),
        ("POST", "/model/delete"),
        ("POST", "/model/new"),
    ]
    new_payload = json.loads(recorder.calls[-1][2])
    assert new_payload["model_name"] == "gpt-4o-mini"
    assert new_payload["litellm_params"]["model"] == "openai/gpt-4o-mini"
    assert new_payload["litellm_params"]["api_key"] == "sk-live-777"
    statuses = {
        model.litellm_model_name: model.sync_status
        for model in await list_models(session)
    }
    assert statuses == {"gpt-4o-mini": "synced", "llama3": "synced"}


async def test_replay_is_idempotent(
    session: AsyncSession,
    seeded_user: User,
    settings: Settings,
) -> None:
    await seed_two_models(session, seeded_user, settings)
    recorder = Recorder()
    assert (
        await sync_models_to_litellm(
            session,
            settings,
            transport=recorder.transport,
        )
        == 1
    )
    assert (
        await sync_models_to_litellm(
            session,
            settings,
            transport=recorder.transport,
        )
        == 1
    )


async def test_proxy_failure_maps_to_upstream_error(
    session: AsyncSession,
    seeded_user: User,
    settings: Settings,
) -> None:
    await seed_two_models(session, seeded_user, settings)

    with pytest.raises(UpstreamError):
        await sync_models_to_litellm(
            session,
            settings,
            transport=Recorder(fail=True).transport,
        )

    assert {
        model.sync_status for model in await list_models(session)
    } == {"error"}


def test_decryption_has_exactly_one_caller() -> None:
    """Enforce the one sanctioned provider-secret plaintext read path."""
    src_root = Path(openrag.__file__).parent
    allowed = {
        src_root / "modules" / "secrets" / "service.py",
        src_root / "modules" / "models" / "sync.py",
    }
    offenders = [
        str(path)
        for path in src_root.rglob("*.py")
        if "_get_secret_decrypted" in path.read_text(encoding="utf-8")
        and path not in allowed
    ]
    assert offenders == []
