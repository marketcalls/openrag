from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.errors import (
    AuthenticationError,
    WorkspaceAccessDenied,
)
from openrag.modules.auth.models import User
from openrag.modules.tenancy.models import (
    Role,
    RolePermission,
    UserRoleBinding,
    Workspace,
    WorkspaceMember,
)
from openrag.modules.tenancy.permissions import ALL_PERMISSIONS

if TYPE_CHECKING:
    from openrag.modules.tenancy.context import TenantContext


@dataclass(frozen=True)
class AuthorizationSnapshot:
    user_id: UUID
    org_id: UUID
    is_platform_superadmin: bool
    org_permissions: frozenset[str]
    workspace_permissions: Mapping[UUID, frozenset[str]]
    workspace_ids: frozenset[UUID]

    def has(self, permission: str, workspace_id: UUID | None = None) -> bool:
        if permission not in ALL_PERMISSIONS:
            return False
        if self.is_platform_superadmin:
            return True
        if permission in self.org_permissions:
            return True
        return workspace_id is not None and permission in self.workspace_permissions.get(
            workspace_id,
            frozenset(),
        )


async def resolve_authorization(
    session: AsyncSession,
    user: User,
) -> AuthorizationSnapshot:
    """Resolve current, tenant-valid capabilities from the database.

    Access-token role/capability claims are deliberately not inputs: revocation and
    binding changes must take effect on the next request.
    """
    if not user.active:
        raise AuthenticationError("unknown or inactive user")

    permission_rows = (
        await session.execute(
            select(UserRoleBinding.workspace_id, RolePermission.permission)
            .join(
                Role,
                and_(
                    Role.id == UserRoleBinding.role_id,
                    Role.org_id == UserRoleBinding.org_id,
                ),
            )
            .join(RolePermission, RolePermission.role_id == Role.id)
            .outerjoin(
                Workspace,
                and_(
                    Workspace.id == UserRoleBinding.workspace_id,
                    Workspace.org_id == UserRoleBinding.org_id,
                ),
            )
            .where(
                UserRoleBinding.user_id == user.id,
                UserRoleBinding.org_id == user.org_id,
                Role.org_id == user.org_id,
                or_(
                    UserRoleBinding.workspace_id.is_(None),
                    Workspace.id.is_not(None),
                ),
            )
        )
    ).all()

    org_permissions: set[str] = set()
    workspace_permissions: defaultdict[UUID, set[str]] = defaultdict(set)
    for workspace_id, permission in permission_rows:
        if permission not in ALL_PERMISSIONS:
            continue
        if workspace_id is None:
            org_permissions.add(permission)
        else:
            workspace_permissions[workspace_id].add(permission)

    workspace_ids = frozenset(
        (
            await session.execute(
                select(WorkspaceMember.workspace_id)
                .join(
                    Workspace,
                    and_(
                        Workspace.id == WorkspaceMember.workspace_id,
                        Workspace.org_id == WorkspaceMember.org_id,
                    ),
                )
                .where(
                    WorkspaceMember.user_id == user.id,
                    WorkspaceMember.org_id == user.org_id,
                    Workspace.org_id == user.org_id,
                )
            )
        )
        .scalars()
        .all()
    )
    immutable_workspace_permissions = MappingProxyType(
        {
            workspace_id: frozenset(permissions)
            for workspace_id, permissions in workspace_permissions.items()
        }
    )
    return AuthorizationSnapshot(
        user_id=user.id,
        org_id=user.org_id,
        is_platform_superadmin=user.is_platform_superadmin,
        org_permissions=frozenset(org_permissions),
        workspace_permissions=immutable_workspace_permissions,
        workspace_ids=workspace_ids,
    )


async def ensure_workspace_access(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    permission: str,
) -> Workspace:
    """Return a tenant-scoped workspace only when capability and ACL both allow it."""
    if permission not in ALL_PERMISSIONS:
        raise WorkspaceAccessDenied("workspace not found or not accessible")

    workspace = (
        await session.execute(
            select(Workspace).where(
                Workspace.id == workspace_id,
                Workspace.org_id == context.org_id,
            )
        )
    ).scalar_one_or_none()
    authorization = context.authorization
    can_read_all = authorization.has("workspace.read_all", workspace_id)
    has_membership = workspace_id in authorization.workspace_ids
    if (
        workspace is None
        or not authorization.has(permission, workspace_id)
        or (not can_read_all and not has_membership)
    ):
        raise WorkspaceAccessDenied("workspace not found or not accessible")
    return workspace
