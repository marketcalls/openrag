"""Credential-safe live RBAC smoke for the running OpenRAG Compose stack."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.config import Settings
from openrag.core.db import build_engine, build_session_factory
from openrag.modules.auth.models import RefreshToken, User
from openrag.modules.auth.passwords import hash_password
from openrag.modules.tenancy.models import (
    Role,
    UserRoleBinding,
    Workspace,
    WorkspaceMember,
)

DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
BOOTSTRAP_CONTAINER = "openrag-bootstrap-1"
SUCCESS_MESSAGES = (
    "health/readiness: PASS",
    "platform login/catalog boundary: PASS",
    "engineer workspace isolation: PASS",
    "administration denials: PASS",
    "logout/refresh: PASS",
)

CommandRunner = Callable[[Sequence[str]], str]
Output = Callable[[str], None]


class SmokeFailure(RuntimeError):
    """A fixed, secret-safe smoke failure suitable for operator output."""


@dataclass(frozen=True)
class BootstrapCredentials:
    email: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class SmokeFixture:
    org_id: UUID
    user_id: UUID
    email: str
    allowed_workspace_id: UUID
    allowed_workspace_name: str
    peer_workspace_id: UUID
    peer_workspace_name: str


class FixtureStore(Protocol):
    async def provision(
        self,
        *,
        bootstrap_email: str,
        engineer_email: str,
        engineer_password: str,
    ) -> SmokeFixture: ...

    async def cleanup(self, fixture: SmokeFixture) -> None: ...


def resolve_api_base_url(environ: Mapping[str, str]) -> str:
    configured = environ.get("OPENRAG_SMOKE_API_URL", "").strip()
    value = configured or DEFAULT_API_BASE_URL
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise SmokeFailure("smoke API URL is invalid")

    loopback_hosts = {"127.0.0.1", "::1", "localhost"}
    if parsed.hostname.casefold() not in loopback_hosts and parsed.scheme != "https":
        raise SmokeFailure("a non-loopback smoke API URL requires secure HTTPS")
    return value.rstrip("/")


def _run_command(argv: Sequence[str]) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed executable and argv list
            list(argv),
            check=True,
            capture_output=True,
            text=True,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise SmokeFailure("bootstrap container inspection failed") from None
    return completed.stdout


def load_bootstrap_credentials(
    run_command: CommandRunner = _run_command,
) -> BootstrapCredentials:
    try:
        raw = run_command(("docker", "inspect", BOOTSTRAP_CONTAINER))
        inspected = json.loads(raw)
        environment = inspected[0]["Config"]["Env"]
        values: dict[str, str] = {}
        for item in environment:
            key, separator, value = item.partition("=")
            if separator and key in {
                "OPENRAG_BOOTSTRAP_EMAIL",
                "OPENRAG_BOOTSTRAP_PASSWORD",
            }:
                values[key] = value
        email = values["OPENRAG_BOOTSTRAP_EMAIL"]
        password = values["OPENRAG_BOOTSTRAP_PASSWORD"]
        if not email or not password:
            raise ValueError
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise SmokeFailure("bootstrap credentials are unavailable") from None
    return BootstrapCredentials(email=email, password=password)


class SqlAlchemyFixtureStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def provision(
        self,
        *,
        bootstrap_email: str,
        engineer_email: str,
        engineer_password: str,
    ) -> SmokeFixture:
        try:
            async with self._session_factory() as session, session.begin():
                bootstrap_user = (
                    await session.execute(
                        select(User).where(User.email == bootstrap_email)
                    )
                ).scalar_one_or_none()
                if bootstrap_user is None or not bootstrap_user.is_platform_superadmin:
                    raise SmokeFailure("bootstrap account is unavailable")
                engineer_role = (
                    await session.execute(
                        select(Role).where(
                            Role.org_id == bootstrap_user.org_id,
                            Role.key == "engineer",
                            Role.is_assignable.is_(True),
                        )
                    )
                ).scalar_one_or_none()
                if engineer_role is None:
                    raise SmokeFailure("Engineer role is unavailable")

                fixture_user = User(
                    org_id=bootstrap_user.org_id,
                    email=engineer_email,
                    password_hash=hash_password(engineer_password),
                )
                session.add(fixture_user)
                await session.flush()
                session.add(
                    UserRoleBinding(
                        org_id=bootstrap_user.org_id,
                        user_id=fixture_user.id,
                        role_id=engineer_role.id,
                        created_by=bootstrap_user.id,
                    )
                )
                suffix = secrets.token_hex(16)
                allowed_workspace = Workspace(
                    org_id=bootstrap_user.org_id,
                    name=f"RBAC smoke allowed {suffix}",
                )
                peer_workspace = Workspace(
                    org_id=bootstrap_user.org_id,
                    name=f"RBAC smoke peer {suffix}",
                )
                session.add_all([allowed_workspace, peer_workspace])
                await session.flush()
                session.add(
                    WorkspaceMember(
                        org_id=bootstrap_user.org_id,
                        workspace_id=allowed_workspace.id,
                        user_id=fixture_user.id,
                    )
                )
                fixture = SmokeFixture(
                    org_id=bootstrap_user.org_id,
                    user_id=fixture_user.id,
                    email=fixture_user.email,
                    allowed_workspace_id=allowed_workspace.id,
                    allowed_workspace_name=allowed_workspace.name,
                    peer_workspace_id=peer_workspace.id,
                    peer_workspace_name=peer_workspace.name,
                )
            return fixture
        except SmokeFailure:
            raise
        except SQLAlchemyError:
            raise SmokeFailure("fixture provisioning failed") from None

    async def cleanup(self, fixture: SmokeFixture) -> None:
        try:
            async with self._session_factory() as session, session.begin():
                fixture_user = (
                    await session.execute(
                        select(User)
                        .where(
                            User.id == fixture.user_id,
                            User.org_id == fixture.org_id,
                            User.email == fixture.email,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                workspace_ids = (
                    fixture.allowed_workspace_id,
                    fixture.peer_workspace_id,
                )
                workspace_names = {
                    fixture.allowed_workspace_id: fixture.allowed_workspace_name,
                    fixture.peer_workspace_id: fixture.peer_workspace_name,
                }
                workspaces = (
                    await session.execute(
                        select(Workspace)
                        .where(
                            Workspace.org_id == fixture.org_id,
                            Workspace.id.in_(workspace_ids),
                        )
                        .with_for_update()
                    )
                ).scalars().all()
                if (
                    fixture_user is None
                    or len(workspace_names) != 2
                    or len(workspaces) != 2
                    or any(
                        workspace_names.get(workspace.id) != workspace.name
                        for workspace in workspaces
                    )
                ):
                    raise SmokeFailure("fixture ownership validation failed")

                await session.execute(
                    delete(RefreshToken).where(
                        RefreshToken.user_id == fixture.user_id
                    )
                )
                await session.execute(
                    delete(WorkspaceMember).where(
                        WorkspaceMember.org_id == fixture.org_id,
                        WorkspaceMember.user_id == fixture.user_id,
                        WorkspaceMember.workspace_id.in_(workspace_ids),
                    )
                )
                await session.execute(
                    delete(UserRoleBinding).where(
                        UserRoleBinding.org_id == fixture.org_id,
                        UserRoleBinding.user_id == fixture.user_id,
                    )
                )
                await session.execute(
                    delete(Workspace).where(
                        Workspace.org_id == fixture.org_id,
                        Workspace.id.in_(workspace_ids),
                    )
                )
                await session.execute(
                    delete(User).where(
                        User.id == fixture.user_id,
                        User.org_id == fixture.org_id,
                        User.email == fixture.email,
                    )
                )
        except SmokeFailure:
            raise
        except SQLAlchemyError:
            raise SmokeFailure("fixture cleanup failed") from None


def require_status(
    step: str,
    response: httpx.Response,
    expected: int,
) -> None:
    if response.status_code != expected:
        raise SmokeFailure(f"{step} failed with HTTP {response.status_code}")


async def _request(
    client: httpx.AsyncClient,
    *,
    step: str,
    method: str,
    path: str,
    expected: int,
    headers: Mapping[str, str] | None = None,
    json_body: object | None = None,
) -> httpx.Response:
    try:
        response = await client.request(
            method,
            path,
            headers=headers,
            json=json_body,
        )
    except httpx.HTTPError:
        raise SmokeFailure(f"{step} request failed") from None
    require_status(step, response, expected)
    return response


def _access_token(step: str, response: httpx.Response) -> str:
    try:
        body = response.json()
        token = body["access_token"]
        if not isinstance(token, str) or not token:
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise SmokeFailure(f"{step} response is invalid") from None
    return token


def _verify_catalog(response: httpx.Response) -> None:
    try:
        body = response.json()
        if not isinstance(body, list):
            raise ValueError
        codes = {
            item["code"]
            for item in body
            if isinstance(item, dict) and isinstance(item.get("code"), str)
        }
        if len(codes) != len(body):
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise SmokeFailure("permission catalog response is invalid") from None
    if "platform.superadmin" in codes:
        raise SmokeFailure("platform privilege appeared in the role catalog")


def _verify_visible_workspace(response: httpx.Response, expected: UUID) -> None:
    try:
        body = response.json()
        if not isinstance(body, list) or len(body) != 1:
            raise ValueError
        visible = UUID(body[0]["id"])
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise SmokeFailure("workspace isolation response is invalid") from None
    if visible != expected:
        raise SmokeFailure("workspace isolation check failed")


async def run_smoke(
    *,
    base_url: str,
    credentials: BootstrapCredentials,
    fixture_store: FixtureStore,
    transport: httpx.AsyncBaseTransport | None = None,
    output: Output = print,
    fixture_password: str | None = None,
) -> None:
    fixture: SmokeFixture | None = None
    suffix = secrets.token_hex(16)
    engineer_email = f"rbac-smoke-{suffix}@example.com"
    engineer_password = fixture_password or secrets.token_urlsafe(32)

    timeout = httpx.Timeout(15.0)
    async with (
        httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        ) as platform_client,
        httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        ) as engineer_client,
    ):
        platform_logout_needed = False
        try:
            await _request(
                platform_client,
                step="health",
                method="GET",
                path="/healthz",
                expected=200,
            )
            await _request(
                platform_client,
                step="readiness",
                method="GET",
                path="/readyz",
                expected=200,
            )
            output(SUCCESS_MESSAGES[0])

            platform_login = await _request(
                platform_client,
                step="platform login",
                method="POST",
                path="/api/v1/auth/login",
                expected=200,
                json_body={
                    "email": credentials.email,
                    "password": credentials.password,
                },
            )
            platform_logout_needed = True
            platform_headers = {
                "Authorization": f"Bearer {_access_token('platform login', platform_login)}"
            }
            catalog = await _request(
                platform_client,
                step="permission catalog",
                method="GET",
                path="/api/v1/roles/catalog",
                expected=200,
                headers=platform_headers,
            )
            _verify_catalog(catalog)
            output(SUCCESS_MESSAGES[1])

            fixture = await fixture_store.provision(
                bootstrap_email=credentials.email,
                engineer_email=engineer_email,
                engineer_password=engineer_password,
            )
            engineer_login = await _request(
                engineer_client,
                step="engineer login",
                method="POST",
                path="/api/v1/auth/login",
                expected=200,
                json_body={
                    "email": fixture.email,
                    "password": engineer_password,
                },
            )
            engineer_headers = {
                "Authorization": f"Bearer {_access_token('engineer login', engineer_login)}"
            }
            visible = await _request(
                engineer_client,
                step="workspace listing",
                method="GET",
                path="/api/v1/workspaces",
                expected=200,
                headers=engineer_headers,
            )
            _verify_visible_workspace(visible, fixture.allowed_workspace_id)
            output(SUCCESS_MESSAGES[2])

            for step, method, path, body in (
                ("role administration denial", "GET", "/api/v1/roles/catalog", None),
                ("user administration denial", "GET", "/api/v1/users", None),
                (
                    "workspace administration denial",
                    "POST",
                    "/api/v1/workspaces",
                    {"name": "RBAC smoke denied"},
                ),
            ):
                await _request(
                    engineer_client,
                    step=step,
                    method=method,
                    path=path,
                    expected=403,
                    headers=engineer_headers,
                    json_body=body,
                )
            output(SUCCESS_MESSAGES[3])

            await _request(
                engineer_client,
                step="engineer logout",
                method="POST",
                path="/api/v1/auth/logout",
                expected=204,
            )
            await _request(
                engineer_client,
                step="refresh after logout",
                method="POST",
                path="/api/v1/auth/refresh",
                expected=401,
            )
            await _request(
                platform_client,
                step="platform logout",
                method="POST",
                path="/api/v1/auth/logout",
                expected=204,
            )
            platform_logout_needed = False
            output(SUCCESS_MESSAGES[4])
        finally:
            if platform_logout_needed:
                try:
                    await platform_client.post("/api/v1/auth/logout")
                except httpx.HTTPError:
                    pass
            if fixture is not None:
                await fixture_store.cleanup(fixture)


async def _async_main() -> None:
    base_url = resolve_api_base_url(os.environ)
    credentials = load_bootstrap_credentials()
    settings = Settings()
    engine = build_engine(settings.database_url)
    try:
        await run_smoke(
            base_url=base_url,
            credentials=credentials,
            fixture_store=SqlAlchemyFixtureStore(build_session_factory(engine)),
        )
    finally:
        await engine.dispose()


def main() -> int:
    try:
        asyncio.run(_async_main())
    except SmokeFailure as exc:
        print(f"RBAC smoke FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("RBAC smoke FAILED: unexpected smoke failure", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
