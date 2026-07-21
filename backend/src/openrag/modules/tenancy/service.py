from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.errors import ConflictError, NotFoundError, WorkspaceAccessDenied
from openrag.modules.audit.service import record_audit
from openrag.modules.auth.models import User
from openrag.modules.documents.enrichment_jobs import (
    enqueue_enrichment_jobs,
    resolve_enrichment_prerequisites,
)
from openrag.modules.evaluations import service as evaluations_service
from openrag.modules.evaluations.automation import workspace_configuration_fingerprint
from openrag.modules.grounding.service import provision_default_grounding_policy
from openrag.modules.models import service as models_service
from openrag.modules.tenancy.authorization import ensure_workspace_access
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import (
    Role,
    RolePermission,
    UserRoleBinding,
    Workspace,
    WorkspaceMember,
)
from openrag.modules.tenancy.permissions import ALL_PERMISSIONS, BUILTIN_ROLE_TEMPLATES
from openrag.modules.tenancy.schemas import RoleOut, WorkspaceMemberOut


async def seed_builtin_roles(
    session: AsyncSession,
    org_id: UUID,
) -> dict[str, Role]:
    """Create missing immutable-key role templates for a new organization."""
    existing = {
        role.key: role
        for role in (
            await session.execute(select(Role).where(Role.org_id == org_id))
        ).scalars()
    }
    for template in BUILTIN_ROLE_TEMPLATES.values():
        if template.key in existing:
            continue
        role = Role(
            org_id=org_id,
            key=template.key,
            name=template.name,
            description=template.description,
            is_system=True,
            is_assignable=True,
        )
        session.add(role)
        await session.flush()
        session.add_all(
            RolePermission(role_id=role.id, permission=permission)
            for permission in template.permissions
        )
        existing[template.key] = role
    await session.flush()
    return existing


async def _permissions_by_role(
    session: AsyncSession,
    role_ids: list[UUID],
) -> dict[UUID, list[str]]:
    if not role_ids:
        return {}
    rows = (
        await session.execute(
            select(RolePermission.role_id, RolePermission.permission)
            .where(RolePermission.role_id.in_(role_ids))
            .order_by(RolePermission.permission)
        )
    ).all()
    result: dict[UUID, list[str]] = {role_id: [] for role_id in role_ids}
    for role_id, permission in rows:
        if permission in ALL_PERMISSIONS:
            result[role_id].append(permission)
    return result


async def roles_to_out(
    session: AsyncSession,
    roles: list[Role],
) -> list[RoleOut]:
    permissions = await _permissions_by_role(session, [role.id for role in roles])
    return [
        RoleOut(
            id=role.id,
            key=role.key,
            name=role.name,
            description=role.description,
            permissions=permissions[role.id],  # type: ignore[arg-type]
            is_system=role.is_system,
            is_assignable=role.is_assignable,
        )
        for role in roles
    ]


async def list_roles(
    session: AsyncSession,
    context: TenantContext,
) -> list[RoleOut]:
    roles = list(
        (
            await session.execute(
                select(Role)
                .where(Role.org_id == context.org_id)
                .order_by(Role.is_system.desc(), Role.name, Role.id)
            )
        ).scalars()
    )
    return await roles_to_out(session, roles)


async def _get_role(
    session: AsyncSession,
    context: TenantContext,
    role_id: UUID,
    *,
    lock: bool = False,
) -> Role:
    statement = select(Role).where(
        Role.id == role_id,
        Role.org_id == context.org_id,
    )
    if lock:
        statement = statement.with_for_update()
    role = (await session.execute(statement)).scalar_one_or_none()
    if role is None:
        raise NotFoundError("role not found")
    return role


async def create_role(
    session: AsyncSession,
    context: TenantContext,
    *,
    name: str,
    description: str,
    permissions: set[str],
) -> RoleOut:
    if not permissions.issubset(ALL_PERMISSIONS):
        raise ConflictError("unknown permission")
    role_id = uuid4()
    role = Role(
        id=role_id,
        org_id=context.org_id,
        key=f"custom_{role_id.hex}",
        name=name,
        description=description,
        is_system=False,
        is_assignable=True,
    )
    session.add(role)
    await session.flush()
    session.add_all(
        RolePermission(role_id=role.id, permission=permission)
        for permission in permissions
    )
    await record_audit(
        session,
        org_id=context.org_id,
        actor_id=context.user_id,
        action="role.created",
        target_type="role",
        target_id=str(role.id),
    )
    await session.commit()
    return (await roles_to_out(session, [role]))[0]


async def update_role(
    session: AsyncSession,
    context: TenantContext,
    role_id: UUID,
    *,
    name: str | None,
    description: str | None,
    permissions: set[str] | None,
) -> RoleOut:
    role = await _get_role(session, context, role_id, lock=True)
    if role.is_system and name is not None and name != role.name:
        raise ConflictError("system role name is protected")
    if permissions is not None and not permissions.issubset(ALL_PERMISSIONS):
        raise ConflictError("unknown permission")
    if role.key == "administrator" and permissions is not None:
        required = {"role.manage", "user.manage"}
        if not required.issubset(permissions):
            raise ConflictError("Administrator must retain role and user management")
    if name is not None:
        role.name = name
    if description is not None:
        role.description = description
    if permissions is not None:
        await session.execute(
            delete(RolePermission).where(RolePermission.role_id == role.id)
        )
        session.add_all(
            RolePermission(role_id=role.id, permission=permission)
            for permission in permissions
        )
    await record_audit(
        session,
        org_id=context.org_id,
        actor_id=context.user_id,
        action="role.updated",
        target_type="role",
        target_id=str(role.id),
    )
    await session.commit()
    return (await roles_to_out(session, [role]))[0]


async def delete_role(
    session: AsyncSession,
    context: TenantContext,
    role_id: UUID,
) -> None:
    role = await _get_role(session, context, role_id, lock=True)
    if role.is_system:
        raise ConflictError("system roles cannot be deleted")
    binding_count = (
        await session.execute(
            select(func.count())
            .select_from(UserRoleBinding)
            .where(
                UserRoleBinding.org_id == context.org_id,
                UserRoleBinding.role_id == role.id,
            )
        )
    ).scalar_one()
    if binding_count:
        raise ConflictError("bound roles cannot be deleted")
    await session.delete(role)
    await record_audit(
        session,
        org_id=context.org_id,
        actor_id=context.user_id,
        action="role.deleted",
        target_type="role",
        target_id=str(role.id),
    )
    await session.commit()


async def _would_remove_last_active_administrator(
    session: AsyncSession,
    *,
    org_id: UUID,
    user_id: UUID,
    replacement_role_ids: set[UUID],
) -> bool:
    administrator = (
        await session.execute(
            select(Role)
            .where(Role.org_id == org_id, Role.key == "administrator")
            .with_for_update()
        )
    ).scalar_one_or_none()
    if administrator is None or administrator.id in replacement_role_ids:
        return False
    target_has_admin = (
        await session.execute(
            select(UserRoleBinding.id).where(
                UserRoleBinding.org_id == org_id,
                UserRoleBinding.user_id == user_id,
                UserRoleBinding.role_id == administrator.id,
                UserRoleBinding.workspace_id.is_(None),
            )
        )
    ).first()
    if target_has_admin is None:
        return False
    active_admin_count = (
        await session.execute(
            select(func.count(func.distinct(UserRoleBinding.user_id)))
            .join(
                User,
                (User.id == UserRoleBinding.user_id)
                & (User.org_id == UserRoleBinding.org_id),
            )
            .where(
                UserRoleBinding.org_id == org_id,
                UserRoleBinding.role_id == administrator.id,
                UserRoleBinding.workspace_id.is_(None),
                User.active.is_(True),
            )
        )
    ).scalar_one()
    return active_admin_count <= 1


async def ensure_can_deactivate_user(
    session: AsyncSession,
    *,
    org_id: UUID,
    user_id: UUID,
) -> None:
    if await _would_remove_last_active_administrator(
        session,
        org_id=org_id,
        user_id=user_id,
        replacement_role_ids=set(),
    ):
        raise ConflictError("the last active Administrator cannot be removed")


async def replace_user_role_bindings(
    session: AsyncSession,
    context: TenantContext,
    user_id: UUID,
    role_ids: list[UUID],
) -> User:
    user = (
        await session.execute(
            select(User)
            .where(
                User.id == user_id,
                User.org_id == context.org_id,
                User.is_platform_superadmin.is_(False),
                User.deleted_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError("user not found")
    roles = list(
        (
            await session.execute(
                select(Role)
                .where(
                    Role.org_id == context.org_id,
                    Role.id.in_(role_ids),
                    Role.is_assignable.is_(True),
                )
                .with_for_update()
            )
        ).scalars()
    )
    if len(roles) != len(role_ids):
        raise NotFoundError("role not found")
    await session.execute(
        select(UserRoleBinding)
        .where(
            UserRoleBinding.org_id == context.org_id,
            UserRoleBinding.user_id == user.id,
            UserRoleBinding.workspace_id.is_(None),
        )
        .with_for_update()
    )
    if user.active and await _would_remove_last_active_administrator(
        session,
        org_id=context.org_id,
        user_id=user.id,
        replacement_role_ids=set(role_ids),
    ):
        raise ConflictError("the last active Administrator cannot be removed")
    await session.execute(
        delete(UserRoleBinding).where(
            UserRoleBinding.org_id == context.org_id,
            UserRoleBinding.user_id == user.id,
            UserRoleBinding.workspace_id.is_(None),
        )
    )
    session.add_all(
        UserRoleBinding(
            org_id=context.org_id,
            user_id=user.id,
            role_id=role_id,
            created_by=context.user_id,
        )
        for role_id in role_ids
    )
    await record_audit(
        session,
        org_id=context.org_id,
        actor_id=context.user_id,
        action="user.role_bindings_replaced",
        target_type="user",
        target_id=str(user.id),
    )
    await session.commit()
    return user


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
    # New workspaces use the governed authority collection end to end. The
    # model-level false default remains for migrated workspaces that have not
    # completed a controlled reindex/cutover yet.
    workspace = Workspace(
        org_id=context.org_id,
        name=name,
        document_authority_enabled=True,
    )
    session.add(workspace)
    await session.flush()
    await provision_default_grounding_policy(
        session,
        org_id=context.org_id,
        workspace_id=workspace.id,
        created_by=context.user_id,
    )
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
        await models_service.resolve_model(
            session,
            requested_model_id=model_id,
            default_model_id=None,
        )
    if workspace.default_model_id == model_id:
        return workspace
    workspace.default_model_id = model_id
    await record_audit(
        session,
        org_id=context.org_id,
        actor_id=context.user_id,
        action="workspace.default_model_changed",
        target_type="workspace",
        target_id=str(workspace.id),
    )
    await session.flush()
    await evaluations_service.queue_config_change_runs(
        session,
        org_id=context.org_id,
        workspace_id=workspace.id,
        configuration_fingerprint=workspace_configuration_fingerprint(
            model_id,
            enrichment_enabled=workspace.enrichment_enabled,
        ),
    )
    return workspace


async def set_enrichment_enabled(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    enabled: bool,
) -> Workspace:
    workspace = await get_workspace(
        session,
        context,
        workspace_id,
        "workspace.manage",
    )
    if workspace.enrichment_enabled == enabled:
        return workspace
    if enabled and await resolve_enrichment_prerequisites(session) is None:
        raise ConflictError(
            "enrichment requires a utility model and active embedding deployment"
        )
    workspace.enrichment_enabled = enabled
    await session.flush()
    if enabled:
        await enqueue_enrichment_jobs(
            session,
            org_id=context.org_id,
            workspace_id=workspace.id,
            requested_by=context.user_id,
            source="backfill",
        )
    await record_audit(
        session,
        org_id=context.org_id,
        actor_id=context.user_id,
        action=(
            "workspace.enrichment_enabled"
            if enabled
            else "workspace.enrichment_disabled"
        ),
        target_type="workspace",
        target_id=str(workspace.id),
    )
    await evaluations_service.queue_config_change_runs(
        session,
        org_id=context.org_id,
        workspace_id=workspace.id,
        configuration_fingerprint=workspace_configuration_fingerprint(
            workspace.default_model_id,
            enrichment_enabled=enabled,
        ),
    )
    return workspace


async def add_member(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    user_id: UUID,
) -> None:
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
                User.deleted_at.is_(None),
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
        WorkspaceMemberOut(user_id=user_id, email=email)
        for user_id, email in rows.all()
    ]
