from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.errors import NotFoundError, WorkspaceAccessDenied
from openrag.modules.audit.service import record_audit
from openrag.modules.auth.models import User
from openrag.modules.models import service as models_service
from openrag.modules.tenancy.authorization import ensure_workspace_access
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import Workspace, WorkspaceMember
from openrag.modules.tenancy.schemas import WorkspaceMemberOut


async def get_workspace(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    permission: str,
) -> Workspace:
    try:
        return await ensure_workspace_access(
            session,
            context,
            workspace_id,
            permission,
        )
    except WorkspaceAccessDenied as exc:
        raise NotFoundError("workspace not found") from exc


async def get_workspace_checked(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    permission: str = "document.read",
) -> Workspace:
    return await ensure_workspace_access(
        session,
        context,
        workspace_id,
        permission,
    )


async def create_workspace(
    session: AsyncSession,
    context: TenantContext,
    name: str,
) -> Workspace:
    if not context.authorization.has("workspace.manage"):
        raise WorkspaceAccessDenied("workspace not found or not accessible")
    workspace = Workspace(org_id=context.org_id, name=name)
    session.add(workspace)
    await session.flush()
    await record_audit(
        session,
        org_id=context.org_id,
        actor_id=context.user_id,
        action="workspace.created",
        target_type="workspace",
        target_id=str(workspace.id),
    )
    await session.commit()
    return workspace


async def list_workspaces(
    session: AsyncSession,
    context: TenantContext,
) -> list[Workspace]:
    statement = select(Workspace).where(Workspace.org_id == context.org_id)
    if not context.authorization.has("workspace.read_all"):
        statement = statement.where(Workspace.id.in_(context.workspace_ids))
    return list(
        (await session.execute(statement.order_by(Workspace.name))).scalars()
    )


async def set_default_model(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    model_id: UUID | None,
) -> Workspace:
    workspace = await get_workspace(
        session,
        context,
        workspace_id,
        "model.configure",
    )
    if model_id is not None:
        await models_service.get_model(session, model_id)
    workspace.default_model_id = model_id
    await session.commit()
    return workspace


async def add_member(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    user_id: UUID,
    role: str,
) -> None:
    del role  # capability assignment moves to role bindings in RBAC Task 4
    await get_workspace(
        session,
        context,
        workspace_id,
        "workspace.manage",
    )

    user = (
        await session.execute(
            select(User).where(
                User.id == user_id,
                User.org_id == context.org_id,
            )
        )
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError("user not found")

    session.add(
        WorkspaceMember(
            org_id=context.org_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )
    )
    await record_audit(
        session,
        org_id=context.org_id,
        actor_id=context.user_id,
        action="workspace.member_added",
        target_type="workspace",
        target_id=f"{workspace_id}:{user_id}",
    )
    await session.commit()


async def list_members(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
) -> list[WorkspaceMemberOut]:
    await get_workspace(
        session,
        context,
        workspace_id,
        "workspace.manage",
    )
    rows = await session.execute(
        select(
            WorkspaceMember.user_id,
            User.email,
        )
        .join(User, User.id == WorkspaceMember.user_id)
        .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
        .where(
            WorkspaceMember.workspace_id == workspace_id,
            Workspace.org_id == context.org_id,
            User.org_id == context.org_id,
        )
        .order_by(User.email)
    )
    return [
        WorkspaceMemberOut(user_id=user_id, email=email, role="member")
        for user_id, email in rows.all()
    ]
