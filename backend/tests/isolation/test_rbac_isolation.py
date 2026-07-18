import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.errors import WorkspaceAccessDenied
from openrag.modules.tenancy.authorization import (
    ensure_workspace_access,
    resolve_authorization,
)
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import WorkspaceMember
from tests.modules.tenancy.test_authorization import (
    bind_role,
    seed_authorization_subject,
)


async def test_workspace_read_all_never_crosses_organization_boundary(
    session: AsyncSession,
) -> None:
    local = await seed_authorization_subject(session, "Isolation Local")
    foreign = await seed_authorization_subject(session, "Isolation Foreign")
    await bind_role(
        session,
        local,
        key="org_auditor",
        permissions={"workspace.read_all", "document.read"},
    )
    await session.commit()
    context = TenantContext(
        user_id=local.user.id,
        org_id=local.organization.id,
        authorization=await resolve_authorization(session, local.user),
    )

    with pytest.raises(WorkspaceAccessDenied):
        await ensure_workspace_access(
            session,
            context,
            foreign.workspaces[0].id,
            "document.read",
        )


async def test_workspace_scoped_hse_manager_never_expands_to_peer_workspace(
    session: AsyncSession,
) -> None:
    hse = await seed_authorization_subject(session, "Scoped HSE")
    await bind_role(
        session,
        hse,
        key="hse_manager",
        permissions={"document.read", "document.upload"},
        workspace=hse.workspaces[0],
    )
    session.add_all(
        WorkspaceMember(
            org_id=hse.organization.id,
            workspace_id=workspace.id,
            user_id=hse.user.id,
        )
        for workspace in hse.workspaces
    )
    await session.commit()
    context = TenantContext(
        user_id=hse.user.id,
        org_id=hse.organization.id,
        authorization=await resolve_authorization(session, hse.user),
    )

    allowed = await ensure_workspace_access(
        session,
        context,
        hse.workspaces[0].id,
        "document.read",
    )
    assert allowed.id == hse.workspaces[0].id
    with pytest.raises(WorkspaceAccessDenied):
        await ensure_workspace_access(
            session,
            context,
            hse.workspaces[1].id,
            "document.read",
        )


async def test_persisted_platform_superadmin_never_crosses_org_object_scope(
    session: AsyncSession,
) -> None:
    platform = await seed_authorization_subject(
        session,
        "Migrated Platform",
        is_platform_superadmin=True,
    )
    foreign = await seed_authorization_subject(session, "Foreign Platform Target")
    await session.commit()
    context = TenantContext(
        user_id=platform.user.id,
        org_id=platform.organization.id,
        authorization=await resolve_authorization(session, platform.user),
    )

    assert context.authorization.is_platform_superadmin is True
    assert context.authorization.has("model.configure") is True
    own = await ensure_workspace_access(
        session,
        context,
        platform.workspaces[0].id,
        "document.read",
    )
    assert own.id == platform.workspaces[0].id
    with pytest.raises(WorkspaceAccessDenied):
        await ensure_workspace_access(
            session,
            context,
            foreign.workspaces[0].id,
            "document.read",
        )
