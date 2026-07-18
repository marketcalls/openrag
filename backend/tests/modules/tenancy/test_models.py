from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import Invitation, User
from openrag.modules.tenancy.models import (
    Organization,
    Role,
    RolePermission,
    UserRoleBinding,
    Workspace,
    WorkspaceMember,
)


@dataclass(frozen=True)
class TenantSeed:
    organization: Organization
    workspace: Workspace
    role: Role
    user: User


async def seed_tenant(session: AsyncSession, label: str) -> TenantSeed:
    organization = Organization(name=f"{label} Org")
    session.add(organization)
    await session.flush()
    workspace = Workspace(org_id=organization.id, name=f"{label} Workspace")
    role = Role(org_id=organization.id, key="user", name=f"{label} User")
    user = User(
        org_id=organization.id,
        email=f"{label.lower()}@example.com",
        password_hash="x",  # noqa: S106 - deliberately inert test fixture
    )
    session.add_all([workspace, role, user])
    await session.flush()
    return TenantSeed(organization, workspace, role, user)


async def test_create_org_and_user(session: AsyncSession) -> None:
    organization = Organization(name="Acme")
    session.add(organization)
    await session.flush()

    user = User(
        org_id=organization.id,
        email="a@acme.com",
        password_hash="x",  # noqa: S106 - deliberately inert test fixture
    )
    session.add(user)
    await session.commit()

    found = (
        await session.execute(select(User).where(User.email == "a@acme.com"))
    ).scalar_one()
    assert found.org_id == organization.id
    assert found.active is True
    assert found.is_platform_superadmin is False


async def test_role_roundtrip(session: AsyncSession) -> None:
    organization = Organization(name="Role Org")
    session.add(organization)
    await session.flush()
    role = Role(
        org_id=organization.id,
        key="engineer",
        name="Engineer",
        description="Contribute knowledge.",
        is_system=True,
        is_assignable=True,
    )
    session.add(role)
    await session.commit()

    found = (await session.execute(select(Role))).scalar_one()
    assert found.id == role.id
    assert found.org_id == organization.id
    assert found.key == "engineer"
    assert found.description == "Contribute knowledge."
    assert found.is_system is True
    assert found.is_assignable is True


async def test_role_permission_roundtrip(session: AsyncSession) -> None:
    organization = Organization(name="Permission Org")
    session.add(organization)
    await session.flush()
    role = Role(
        org_id=organization.id,
        key="user",
        name="User",
    )
    session.add(role)
    await session.flush()
    permission = RolePermission(role_id=role.id, permission="chat.use")
    session.add(permission)
    await session.commit()

    found = (await session.execute(select(RolePermission))).scalar_one()
    assert found.role_id == role.id
    assert found.permission == "chat.use"


async def test_user_role_binding_roundtrip(session: AsyncSession) -> None:
    organization = Organization(name="Binding Org")
    session.add(organization)
    await session.flush()
    workspace = Workspace(org_id=organization.id, name="Operations")
    role = Role(
        org_id=organization.id,
        key="hse_manager",
        name="HSE Manager",
    )
    user = User(
        org_id=organization.id,
        email="manager@binding.example.com",
        password_hash="x",  # noqa: S106 - deliberately inert test fixture
    )
    session.add_all([workspace, role, user])
    await session.flush()
    binding = UserRoleBinding(
        org_id=organization.id,
        user_id=user.id,
        role_id=role.id,
        workspace_id=workspace.id,
        created_by=user.id,
    )
    session.add(binding)
    await session.commit()

    found = (await session.execute(select(UserRoleBinding))).scalar_one()
    assert found.id == binding.id
    assert found.org_id == organization.id
    assert found.user_id == user.id
    assert found.role_id == role.id
    assert found.workspace_id == workspace.id
    assert found.created_by == user.id


async def test_duplicate_role_keys_are_rejected_per_organization(
    session: AsyncSession,
) -> None:
    organization = Organization(name="Unique Role Org")
    session.add(organization)
    await session.flush()
    session.add_all(
        [
            Role(
                org_id=organization.id,
                key="engineer",
                name="Engineer",
            ),
            Role(
                org_id=organization.id,
                key="engineer",
                name="Duplicate Engineer",
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        await session.commit()


async def test_invitation_references_a_persisted_role(session: AsyncSession) -> None:
    organization = Organization(name="Invitation Org")
    session.add(organization)
    await session.flush()
    role = Role(
        org_id=organization.id,
        key="user",
        name="User",
    )
    session.add(role)
    await session.flush()
    invitation = Invitation(
        org_id=organization.id,
        email="invitee@example.com",
        role_id=role.id,
        token_hash="invitation-token",  # noqa: S106 - inert persisted test value
        expires_at=datetime(2030, 1, 1),
    )
    session.add(invitation)
    await session.commit()

    found = (await session.execute(select(Invitation))).scalar_one()
    assert found.role_id == role.id


def test_workspace_member_does_not_accept_a_string_role() -> None:
    assert "role" not in WorkspaceMember.__table__.columns
    with pytest.raises(TypeError, match="'role' is an invalid keyword argument"):
        WorkspaceMember(
            workspace_id=uuid4(),
            user_id=uuid4(),
            role="administrator",
        )


async def test_workspace_member_persists_organization_scope(
    session: AsyncSession,
) -> None:
    tenant = await seed_tenant(session, "Member Scope")
    member = WorkspaceMember(
        org_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        user_id=tenant.user.id,
    )
    session.add(member)
    await session.commit()

    found = (await session.execute(select(WorkspaceMember))).scalar_one()
    assert found.org_id == tenant.organization.id


@pytest.mark.parametrize(
    "mismatch",
    ["user_id", "role_id", "workspace_id", "created_by"],
)
async def test_cross_org_user_role_binding_is_rejected(
    session: AsyncSession,
    mismatch: Literal["user_id", "role_id", "workspace_id", "created_by"],
) -> None:
    local = await seed_tenant(session, "Binding Local")
    foreign = await seed_tenant(session, "Binding Foreign")
    binding = UserRoleBinding(
        org_id=local.organization.id,
        user_id=foreign.user.id if mismatch == "user_id" else local.user.id,
        role_id=foreign.role.id if mismatch == "role_id" else local.role.id,
        workspace_id=(
            foreign.workspace.id if mismatch == "workspace_id" else local.workspace.id
        ),
        created_by=foreign.user.id if mismatch == "created_by" else local.user.id,
    )
    session.add(binding)

    with pytest.raises(IntegrityError):
        await session.commit()


async def test_cross_org_invitation_is_rejected(session: AsyncSession) -> None:
    local = await seed_tenant(session, "Invitation Local")
    foreign = await seed_tenant(session, "Invitation Foreign")
    invitation = Invitation(
        org_id=local.organization.id,
        email="cross-org-invite@example.com",
        role_id=foreign.role.id,
        token_hash="cross-org-token",  # noqa: S106 - inert persisted test value
        expires_at=datetime(2030, 1, 1),
    )
    session.add(invitation)

    with pytest.raises(IntegrityError):
        await session.commit()


async def test_cross_org_workspace_membership_is_rejected(
    session: AsyncSession,
) -> None:
    local = await seed_tenant(session, "Membership Local")
    foreign = await seed_tenant(session, "Membership Foreign")
    member = WorkspaceMember(
        org_id=local.organization.id,
        workspace_id=local.workspace.id,
        user_id=foreign.user.id,
    )
    session.add(member)

    with pytest.raises(IntegrityError):
        await session.commit()


async def test_duplicate_organization_wide_bindings_are_rejected(
    session: AsyncSession,
) -> None:
    tenant = await seed_tenant(session, "Duplicate Org Binding")
    session.add_all(
        [
            UserRoleBinding(
                org_id=tenant.organization.id,
                user_id=tenant.user.id,
                role_id=tenant.role.id,
            ),
            UserRoleBinding(
                org_id=tenant.organization.id,
                user_id=tenant.user.id,
                role_id=tenant.role.id,
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        await session.commit()


async def test_duplicate_workspace_scoped_bindings_are_rejected(
    session: AsyncSession,
) -> None:
    tenant = await seed_tenant(session, "Duplicate Workspace Binding")
    session.add_all(
        [
            UserRoleBinding(
                org_id=tenant.organization.id,
                user_id=tenant.user.id,
                role_id=tenant.role.id,
                workspace_id=tenant.workspace.id,
            ),
            UserRoleBinding(
                org_id=tenant.organization.id,
                user_id=tenant.user.id,
                role_id=tenant.role.id,
                workspace_id=tenant.workspace.id,
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        await session.commit()


async def test_unknown_permission_is_rejected(session: AsyncSession) -> None:
    tenant = await seed_tenant(session, "Unknown Permission")
    session.add(RolePermission(role_id=tenant.role.id, permission="unknown.use"))

    with pytest.raises(IntegrityError):
        await session.commit()


async def test_same_role_key_is_allowed_in_different_organizations(
    session: AsyncSession,
) -> None:
    first = await seed_tenant(session, "First Role Key")
    second = await seed_tenant(session, "Second Role Key")
    await session.commit()

    roles = (
        await session.execute(select(Role).where(Role.key == "user").order_by(Role.org_id))
    ).scalars().all()
    assert {role.org_id for role in roles} == {
        first.organization.id,
        second.organization.id,
    }


async def test_rbac_model_defaults_are_persisted(session: AsyncSession) -> None:
    tenant = await seed_tenant(session, "Default Values")
    binding = UserRoleBinding(
        org_id=tenant.organization.id,
        user_id=tenant.user.id,
        role_id=tenant.role.id,
    )
    session.add(binding)
    await session.commit()

    assert tenant.role.description == ""
    assert tenant.role.is_system is False
    assert tenant.role.is_assignable is True
    assert tenant.user.is_platform_superadmin is False
    assert tenant.user.active is True
    assert binding.workspace_id is None
    assert binding.created_by is None
