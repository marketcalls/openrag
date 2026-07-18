import json
from collections.abc import Sequence
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from openrag.core.db import build_session_factory, naive_utc
from openrag.modules.auth.models import RefreshToken, User
from openrag.modules.auth.passwords import verify_password
from openrag.modules.tenancy.models import Organization, Workspace, WorkspaceMember
from scripts import rbac_compose_smoke as smoke

ADMIN_TOKEN = "admin-token-sentinel"  # noqa: S105 - inert redaction sentinel
ENGINEER_TOKEN = "engineer-token-sentinel"  # noqa: S105 - inert test sentinel
ADMIN_PASSWORD = "admin-password-sentinel"  # noqa: S105 - inert test sentinel
ENGINEER_PASSWORD = "engineer-password-sentinel"  # noqa: S105 - inert test sentinel
ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
ALLOWED_WORKSPACE = UUID("33333333-3333-4333-8333-333333333333")
PEER_WORKSPACE = UUID("44444444-4444-4444-8444-444444444444")


class FakeFixtureStore:
    def __init__(self) -> None:
        self.provisioned: list[tuple[str, str, str]] = []
        self.cleaned: list[
            tuple[smoke.SmokeFixture, tuple[UUID, ...], tuple[str, ...]]
        ] = []

    async def provision(
        self,
        *,
        bootstrap_email: str,
        engineer_email: str,
        engineer_password: str,
    ) -> smoke.SmokeFixture:
        self.provisioned.append(
            (bootstrap_email, engineer_email, engineer_password)
        )
        return smoke.SmokeFixture(
            org_id=ORG_ID,
            user_id=USER_ID,
            email=engineer_email,
        )

    async def cleanup(
        self,
        fixture: smoke.SmokeFixture,
        workspace_ids: Sequence[UUID],
        workspace_names: Sequence[str],
    ) -> None:
        self.cleaned.append(
            (fixture, tuple(workspace_ids), tuple(workspace_names))
        )


async def promote_seeded_bootstrap(
    engine: AsyncEngine,
    user_id: UUID,
) -> None:
    factory = build_session_factory(engine)
    async with factory() as session, session.begin():
        user = await session.get(User, user_id)
        assert user is not None
        user.is_platform_superadmin = True


def test_default_api_url_is_loopback() -> None:
    assert smoke.resolve_api_base_url({}) == "http://127.0.0.1:8000"


def test_remote_explicit_api_url_requires_https() -> None:
    with pytest.raises(smoke.SmokeFailure, match="secure HTTPS"):
        smoke.resolve_api_base_url(
            {"OPENRAG_SMOKE_API_URL": "http://api.example.com"}
        )

    assert (
        smoke.resolve_api_base_url(
            {"OPENRAG_SMOKE_API_URL": "https://api.example.com"}
        )
        == "https://api.example.com"
    )


def test_bootstrap_inspection_uses_fixed_argv_and_redacts_parse_failures() -> None:
    argv_seen: list[str] = []
    secret_email = "secret-email@example.com"  # noqa: S105 - inert test sentinel

    def malformed_runner(argv: Sequence[str]) -> str:
        argv_seen.extend(argv)
        return json.dumps(
            [{"Config": {"Env": [f"OPENRAG_BOOTSTRAP_EMAIL={secret_email}"]}}]
        )

    with pytest.raises(smoke.SmokeFailure) as captured:
        smoke.load_bootstrap_credentials(malformed_runner)

    assert argv_seen == ["docker", "inspect", "openrag-bootstrap-1"]
    assert secret_email not in str(captured.value)
    assert ADMIN_PASSWORD not in str(captured.value)
    assert str(captured.value) == "bootstrap credentials are unavailable"


def test_bootstrap_credentials_hide_password_from_repr() -> None:
    credentials = smoke.BootstrapCredentials(
        email="root@example.com",
        password=ADMIN_PASSWORD,
    )

    assert ADMIN_PASSWORD not in repr(credentials)
    assert "root@example.com" in repr(credentials)


def api_handler(
    *, fail_second_workspace: bool = False
) -> tuple[httpx.MockTransport, list[tuple[str, str]]]:
    calls: list[tuple[str, str]] = []
    workspace_creates = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal workspace_creates
        calls.append((request.method, request.url.path))
        authorization = request.headers.get("authorization", "")

        if request.url.path in {"/healthz", "/readyz"}:
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/api/v1/auth/login":
            body = json.loads(request.content)
            if body["email"] == "root@example.com":
                return httpx.Response(
                    200,
                    json={"access_token": ADMIN_TOKEN},
                    headers={
                        "set-cookie": "refresh_token=admin-refresh; Path=/api/v1/auth; HttpOnly"
                    },
                )
            return httpx.Response(
                200,
                json={"access_token": ENGINEER_TOKEN},
                headers={
                    "set-cookie": "refresh_token=engineer-refresh; Path=/api/v1/auth; HttpOnly"
                },
            )
        if request.url.path == "/api/v1/roles/catalog":
            if authorization == f"Bearer {ENGINEER_TOKEN}":
                return httpx.Response(403, json={"detail": ENGINEER_PASSWORD})
            return httpx.Response(
                200,
                json=[{"code": "role.manage"}, {"code": "chat.use"}],
            )
        if request.url.path == "/api/v1/users":
            return httpx.Response(403, json={"detail": ENGINEER_PASSWORD})
        if request.url.path == "/api/v1/workspaces" and request.method == "POST":
            if authorization == f"Bearer {ENGINEER_TOKEN}":
                return httpx.Response(403, json={"detail": ENGINEER_PASSWORD})
            workspace_creates += 1
            if fail_second_workspace and workspace_creates == 2:
                return httpx.Response(
                    500,
                    json={"detail": f"{ADMIN_PASSWORD} {ADMIN_TOKEN}"},
                )
            workspace_id = (
                ALLOWED_WORKSPACE if workspace_creates == 1 else PEER_WORKSPACE
            )
            return httpx.Response(201, json={"id": str(workspace_id)})
        if request.url.path.endswith("/members"):
            return httpx.Response(204)
        if request.url.path == "/api/v1/workspaces" and request.method == "GET":
            return httpx.Response(
                200,
                json=[{"id": str(ALLOWED_WORKSPACE), "name": "Allowed"}],
            )
        if request.url.path == "/api/v1/auth/logout":
            return httpx.Response(
                204,
                headers={
                    "set-cookie": "refresh_token=; Max-Age=0; Path=/api/v1/auth; HttpOnly"
                },
            )
        if request.url.path == "/api/v1/auth/refresh":
            assert "refresh_token" not in request.headers.get("cookie", "")
            return httpx.Response(401, json={"detail": "missing refresh token"})
        raise AssertionError(f"unexpected test request: {request.method} {request.url.path}")

    return httpx.MockTransport(handler), calls


async def test_smoke_calls_real_contract_and_always_cleans_owned_fixtures(
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport, calls = api_handler()
    store = FakeFixtureStore()

    await smoke.run_smoke(
        base_url="http://127.0.0.1:8000",
        credentials=smoke.BootstrapCredentials(
            email="root@example.com",
            password=ADMIN_PASSWORD,
        ),
        fixture_store=store,
        transport=transport,
        output=print,
        fixture_password=ENGINEER_PASSWORD,
    )

    assert store.provisioned[0][0] == "root@example.com"
    assert len(store.cleaned) == 1
    cleaned_fixture, cleaned_ids, cleaned_names = store.cleaned[0]
    assert cleaned_fixture == smoke.SmokeFixture(
        org_id=ORG_ID,
        user_id=USER_ID,
        email=store.provisioned[0][1],
    )
    assert cleaned_ids == (ALLOWED_WORKSPACE, PEER_WORKSPACE)
    assert len(cleaned_names) == 2
    assert cleaned_names[0].startswith("RBAC smoke allowed ")
    assert cleaned_names[1].startswith("RBAC smoke peer ")
    assert ("GET", "/healthz") in calls
    assert ("GET", "/readyz") in calls
    assert calls.count(("POST", "/api/v1/auth/logout")) == 2
    output = capsys.readouterr().out
    for secret in (
        ADMIN_PASSWORD,
        ENGINEER_PASSWORD,
        ADMIN_TOKEN,
        ENGINEER_TOKEN,
    ):
        assert secret not in output
    assert output.splitlines() == list(smoke.SUCCESS_MESSAGES)


async def test_partial_api_failure_is_redacted_and_cleans_partial_fixture(
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport, _ = api_handler(fail_second_workspace=True)
    store = FakeFixtureStore()

    with pytest.raises(smoke.SmokeFailure) as captured:
        await smoke.run_smoke(
            base_url="http://127.0.0.1:8000",
            credentials=smoke.BootstrapCredentials(
                email="root@example.com",
                password=ADMIN_PASSWORD,
            ),
            fixture_store=store,
            transport=transport,
            output=print,
            fixture_password=ENGINEER_PASSWORD,
        )

    assert str(captured.value) == "peer workspace creation failed with HTTP 500"
    assert store.cleaned[0][1] == (ALLOWED_WORKSPACE,)
    assert len(store.cleaned[0][2]) == 2
    combined = str(captured.value) + capsys.readouterr().out
    for secret in (
        ADMIN_PASSWORD,
        ENGINEER_PASSWORD,
        ADMIN_TOKEN,
        ENGINEER_TOKEN,
    ):
        assert secret not in combined


async def test_sqlalchemy_store_cleanup_is_organization_scoped(
    engine: AsyncEngine,
    seeded_user: User,
) -> None:
    factory = build_session_factory(engine)
    await promote_seeded_bootstrap(engine, seeded_user.id)
    store = smoke.SqlAlchemyFixtureStore(factory)
    fixture_email = f"rbac-store-{uuid4().hex}@example.com"
    fixture = await store.provision(
        bootstrap_email=seeded_user.email,
        engineer_email=fixture_email,
        engineer_password=ENGINEER_PASSWORD,
    )

    async with factory() as session:
        cleanup_name = f"Owned smoke workspace {uuid4().hex}"
        own_workspace = Workspace(org_id=fixture.org_id, name=cleanup_name)
        foreign_org = Organization(name=f"Foreign {uuid4().hex}")
        session.add_all([own_workspace, foreign_org])
        await session.flush()
        foreign_workspace = Workspace(
            org_id=foreign_org.id,
            name=cleanup_name,
        )
        session.add(foreign_workspace)
        await session.flush()
        session.add(
            WorkspaceMember(
                org_id=fixture.org_id,
                workspace_id=own_workspace.id,
                user_id=fixture.user_id,
            )
        )
        session.add(
            RefreshToken(
                user_id=fixture.user_id,
                family_id=uuid4(),
                token_hash=uuid4().hex,
                expires_at=naive_utc() + timedelta(hours=1),
            )
        )
        await session.commit()
        own_workspace_id = own_workspace.id
        foreign_workspace_id = foreign_workspace.id

    await store.cleanup(
        fixture,
        [foreign_workspace_id],
        [cleanup_name],
    )

    async with factory() as session:
        assert await session.get(User, fixture.user_id) is None
        assert await session.get(Workspace, own_workspace_id) is None
        assert await session.get(Workspace, foreign_workspace_id) is not None
        existing_admin = await session.get(User, seeded_user.id)
        assert existing_admin is not None
        assert existing_admin.email == seeded_user.email
        fixture_user = (
            await session.execute(select(User).where(User.email == fixture_email))
        ).scalar_one_or_none()
        assert fixture_user is None


async def test_sqlalchemy_store_hashes_generated_fixture_password(
    engine: AsyncEngine,
    seeded_user: User,
) -> None:
    factory = build_session_factory(engine)
    await promote_seeded_bootstrap(engine, seeded_user.id)
    store = smoke.SqlAlchemyFixtureStore(factory)
    fixture = await store.provision(
        bootstrap_email=seeded_user.email,
        engineer_email=f"rbac-password-{uuid4().hex}@example.com",
        engineer_password=ENGINEER_PASSWORD,
    )
    try:
        async with factory() as session:
            fixture_user = await session.get(User, fixture.user_id)
            assert fixture_user is not None
            assert fixture_user.password_hash != ENGINEER_PASSWORD
            assert verify_password(fixture_user.password_hash, ENGINEER_PASSWORD)
    finally:
        await store.cleanup(fixture, [], [])
