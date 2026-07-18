import json
from collections.abc import Sequence
from dataclasses import replace
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from openrag.core.db import build_session_factory
from openrag.modules.auth.models import User
from openrag.modules.auth.passwords import verify_password
from openrag.modules.tenancy.models import UserRoleBinding, Workspace, WorkspaceMember
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
        self.cleaned: list[smoke.SmokeFixture] = []

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
            allowed_workspace_id=ALLOWED_WORKSPACE,
            allowed_workspace_name="RBAC smoke allowed fixture",
            peer_workspace_id=PEER_WORKSPACE,
            peer_workspace_name="RBAC smoke peer fixture",
        )

    async def cleanup(self, fixture: smoke.SmokeFixture) -> None:
        self.cleaned.append(fixture)


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
    *, fail_workspace_listing: bool = False
) -> tuple[httpx.MockTransport, list[tuple[str, str]]]:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
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
            assert authorization == f"Bearer {ENGINEER_TOKEN}"
            return httpx.Response(403, json={"detail": ENGINEER_PASSWORD})
        if request.url.path == "/api/v1/workspaces" and request.method == "GET":
            if fail_workspace_listing:
                return httpx.Response(
                    500,
                    json={"detail": f"{ADMIN_PASSWORD} {ADMIN_TOKEN}"},
                )
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
    assert store.cleaned[0] == smoke.SmokeFixture(
        org_id=ORG_ID,
        user_id=USER_ID,
        email=store.provisioned[0][1],
        allowed_workspace_id=ALLOWED_WORKSPACE,
        allowed_workspace_name="RBAC smoke allowed fixture",
        peer_workspace_id=PEER_WORKSPACE,
        peer_workspace_name="RBAC smoke peer fixture",
    )
    assert ("GET", "/healthz") in calls
    assert ("GET", "/readyz") in calls
    assert calls.count(("POST", "/api/v1/workspaces")) == 1
    assert not any(path.endswith("/members") for _, path in calls)
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
    transport, _ = api_handler(fail_workspace_listing=True)
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

    assert str(captured.value) == "workspace listing failed with HTTP 500"
    assert store.cleaned == [
        smoke.SmokeFixture(
            org_id=ORG_ID,
            user_id=USER_ID,
            email=store.provisioned[0][1],
            allowed_workspace_id=ALLOWED_WORKSPACE,
            allowed_workspace_name="RBAC smoke allowed fixture",
            peer_workspace_id=PEER_WORKSPACE,
            peer_workspace_name="RBAC smoke peer fixture",
        )
    ]
    combined = str(captured.value) + capsys.readouterr().out
    for secret in (
        ADMIN_PASSWORD,
        ENGINEER_PASSWORD,
        ADMIN_TOKEN,
        ENGINEER_TOKEN,
    ):
        assert secret not in combined


async def test_sqlalchemy_store_cleanup_preserves_same_org_same_name_workspace(
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

    async with factory() as session, session.begin():
        same_name_workspace = Workspace(
            org_id=fixture.org_id,
            name=fixture.allowed_workspace_name,
        )
        session.add(same_name_workspace)
        await session.flush()
        same_name_workspace_id = same_name_workspace.id

    async with factory() as session:
        memberships = (
            await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.user_id == fixture.user_id
                )
            )
        ).scalars().all()
        assert [membership.workspace_id for membership in memberships] == [
            fixture.allowed_workspace_id
        ]

    await store.cleanup(fixture)

    async with factory() as session:
        assert await session.get(User, fixture.user_id) is None
        assert await session.get(Workspace, fixture.allowed_workspace_id) is None
        assert await session.get(Workspace, fixture.peer_workspace_id) is None
        assert await session.get(Workspace, same_name_workspace_id) is not None
        existing_admin = await session.get(User, seeded_user.id)
        assert existing_admin is not None
        assert existing_admin.email == seeded_user.email
        fixture_user = (
            await session.execute(select(User).where(User.email == fixture_email))
        ).scalar_one_or_none()
        assert fixture_user is None


async def test_sqlalchemy_store_cleanup_rejects_arbitrary_workspace_id(
    engine: AsyncEngine,
    seeded_user: User,
) -> None:
    factory = build_session_factory(engine)
    await promote_seeded_bootstrap(engine, seeded_user.id)
    store = smoke.SqlAlchemyFixtureStore(factory)
    fixture = await store.provision(
        bootstrap_email=seeded_user.email,
        engineer_email=f"rbac-tamper-{uuid4().hex}@example.com",
        engineer_password=ENGINEER_PASSWORD,
    )
    async with factory() as session, session.begin():
        arbitrary_workspace = Workspace(
            org_id=fixture.org_id,
            name=f"Arbitrary workspace {uuid4().hex}",
        )
        session.add(arbitrary_workspace)
        await session.flush()
        arbitrary_workspace_id = arbitrary_workspace.id

    tampered_fixture = replace(
        fixture,
        allowed_workspace_id=arbitrary_workspace_id,
    )
    with pytest.raises(
        smoke.SmokeFailure,
        match="^fixture ownership validation failed$",
    ):
        await store.cleanup(tampered_fixture)

    async with factory() as session:
        assert await session.get(User, fixture.user_id) is not None
        assert await session.get(Workspace, arbitrary_workspace_id) is not None
        assert await session.get(Workspace, fixture.allowed_workspace_id) is not None
        assert await session.get(Workspace, fixture.peer_workspace_id) is not None
        bindings = (
            await session.execute(
                select(UserRoleBinding).where(
                    UserRoleBinding.user_id == fixture.user_id
                )
            )
        ).scalars().all()
        assert bindings

    await store.cleanup(fixture)


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
        await store.cleanup(fixture)
