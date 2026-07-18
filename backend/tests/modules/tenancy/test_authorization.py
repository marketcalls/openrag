from dataclasses import dataclass, replace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.errors import (
    AuthenticationError,
    NotFoundError,
    WorkspaceAccessDenied,
)
from openrag.modules.auth.models import User
from openrag.modules.tenancy.authorization import (
    ensure_workspace_access,
    resolve_authorization,
)
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import (
    Organization,
    Role,
    RolePermission,
    UserRoleBinding,
    Workspace,
    WorkspaceMember,
)
from openrag.modules.tenancy.service import get_workspace, list_workspaces


@dataclass(frozen=True)
class AuthorizationSeed:
    organization: Organization
    user: User
    workspaces: tuple[Workspace, Workspace]


async def seed_authorization_subject(
    session: AsyncSession,
    label: str,
    *,
    is_platform_superadmin: bool = False,
    active: bool = True,
) -> AuthorizationSeed:
    organization = Organization(name=f"{label} Org")
    session.add(organization)
    await session.flush()
    user = User(
        org_id=organization.id,
        email=f"{label.lower()}@example.com",
        password_hash="x",  # noqa: S106 - inert test value
        is_platform_superadmin=is_platform_superadmin,
        active=active,
    )
    workspaces = (
        Workspace(org_id=organization.id, name=f"{label} One"),
        Workspace(org_id=organization.id, name=f"{label} Two"),
    )
    session.add_all([user, *workspaces])
    await session.flush()
    return AuthorizationSeed(organization, user, workspaces)


async def bind_role(
    session: AsyncSession,
    seed: AuthorizationSeed,
    *,
    key: str,
    permissions: set[str],
    workspace: Workspace | None = None,
) -> Role:
    role = Role(
        org_id=seed.organization.id,
        key=key,
        name=key.replace("_", " ").title(),
    )
    session.add(role)
    await session.flush()
    session.add_all(
        RolePermission(role_id=role.id, permission=permission)
        for permission in permissions
    )
    session.add(
        UserRoleBinding(
            org_id=seed.organization.id,
            user_id=seed.user.id,
            role_id=role.id,
            workspace_id=workspace.id if workspace else None,
        )
    )
    await session.flush()
    return role


async def context_for(
    session: AsyncSession,
    seed: AuthorizationSeed,
) -> TenantContext:
    authorization = await resolve_authorization(session, seed.user)
    return TenantContext(
        user_id=seed.user.id,
        org_id=seed.organization.id,
        authorization=authorization,
    )


async def test_custom_role_name_without_permissions_grants_no_workspace_access(
    session: AsyncSession,
) -> None:
    seed = await seed_authorization_subject(session, "Custom Empty")
    await bind_role(session, seed, key="executive_owner", permissions=set())
    await session.commit()

    context = await context_for(session, seed)

    assert await list_workspaces(session, context) == []


async def test_org_document_permission_does_not_bypass_workspace_membership(
    session: AsyncSession,
) -> None:
    seed = await seed_authorization_subject(session, "Org Reader")
    await bind_role(
        session,
        seed,
        key="reader",
        permissions={"document.read"},
    )
    await session.commit()
    context = await context_for(session, seed)

    with pytest.raises(WorkspaceAccessDenied):
        await ensure_workspace_access(
            session,
            context,
            seed.workspaces[0].id,
            "document.read",
        )


async def test_workspace_read_all_lists_only_the_subject_organization(
    session: AsyncSession,
) -> None:
    seed = await seed_authorization_subject(session, "Read All")
    foreign = await seed_authorization_subject(session, "Foreign Read All")
    await bind_role(
        session,
        seed,
        key="auditor",
        permissions={"workspace.read_all"},
    )
    await session.commit()
    context = await context_for(session, seed)

    visible = await list_workspaces(session, context)

    assert {workspace.id for workspace in visible} == {
        workspace.id for workspace in seed.workspaces
    }
    assert not {workspace.id for workspace in visible}.intersection(
        workspace.id for workspace in foreign.workspaces
    )


async def test_workspace_scoped_permission_applies_only_to_bound_workspace(
    session: AsyncSession,
) -> None:
    seed = await seed_authorization_subject(session, "Scoped Reader")
    await bind_role(
        session,
        seed,
        key="scoped_reader",
        permissions={"document.read"},
        workspace=seed.workspaces[0],
    )
    session.add_all(
        WorkspaceMember(
            org_id=seed.organization.id,
            workspace_id=workspace.id,
            user_id=seed.user.id,
        )
        for workspace in seed.workspaces
    )
    await session.commit()
    context = await context_for(session, seed)

    allowed = await ensure_workspace_access(
        session,
        context,
        seed.workspaces[0].id,
        "document.read",
    )
    assert allowed.id == seed.workspaces[0].id
    with pytest.raises(WorkspaceAccessDenied):
        await ensure_workspace_access(
            session,
            context,
            seed.workspaces[1].id,
            "document.read",
        )


async def test_platform_superadmin_still_lists_only_deliberate_org_scope(
    session: AsyncSession,
) -> None:
    seed = await seed_authorization_subject(
        session,
        "Platform Scope",
        is_platform_superadmin=True,
    )
    foreign = await seed_authorization_subject(session, "Foreign Platform Scope")
    await session.commit()
    context = await context_for(session, seed)

    assert context.authorization.has("rag.evaluate")
    visible = await list_workspaces(session, context)
    assert {workspace.id for workspace in visible} == {
        workspace.id for workspace in seed.workspaces
    }
    assert not {workspace.id for workspace in visible}.intersection(
        workspace.id for workspace in foreign.workspaces
    )


async def test_chat_capability_can_open_workspace_without_manage_capability(
    session: AsyncSession,
) -> None:
    seed = await seed_authorization_subject(session, "Chat Reader")
    await bind_role(
        session,
        seed,
        key="chat_reader",
        permissions={"chat.use"},
    )
    session.add(
        WorkspaceMember(
            org_id=seed.organization.id,
            workspace_id=seed.workspaces[0].id,
            user_id=seed.user.id,
        )
    )
    await session.commit()
    context = await context_for(session, seed)

    workspace = await get_workspace(
        session,
        context,
        seed.workspaces[0].id,
        "chat.use",
    )
    assert workspace.id == seed.workspaces[0].id
    with pytest.raises(NotFoundError):
        await get_workspace(
            session,
            context,
            seed.workspaces[0].id,
            "workspace.manage",
        )


async def test_inactive_user_is_rejected_before_permission_resolution(
    session: AsyncSession,
) -> None:
    seed = await seed_authorization_subject(session, "Inactive", active=False)
    await session.commit()

    with pytest.raises(AuthenticationError, match="inactive"):
        await resolve_authorization(session, seed.user)


async def test_tenant_context_rejects_mismatched_authorization_snapshot(
    session: AsyncSession,
) -> None:
    seed = await seed_authorization_subject(session, "Context Integrity")
    await session.commit()
    authorization = await resolve_authorization(session, seed.user)

    with pytest.raises(ValueError, match="does not match"):
        TenantContext(
            user_id=seed.user.id,
            org_id=seed.organization.id,
            authorization=replace(authorization, org_id=seed.workspaces[0].id),
        )
