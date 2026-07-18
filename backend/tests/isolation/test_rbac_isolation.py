import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.errors import WorkspaceAccessDenied
from openrag.modules.tenancy.authorization import (
    ensure_workspace_access,
    resolve_authorization,
)
from openrag.modules.tenancy.context import TenantContext
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
