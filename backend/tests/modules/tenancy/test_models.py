from datetime import datetime
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
