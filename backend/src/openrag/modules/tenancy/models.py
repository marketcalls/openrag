from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk
from openrag.modules.tenancy.permissions import ALL_PERMISSIONS

_PERMISSION_CHECK_VALUES = ", ".join(
    f"'{permission}'" for permission in sorted(ALL_PERMISSIONS)
)


class Organization(UUIDPk, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(unique=True)


class Workspace(UUIDPk, Base):
    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("org_id", "id", name="uq_workspaces_org_id"),)

    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"),
        index=True,
    )
    name: Mapped[str]
    embedding_model: Mapped[str] = mapped_column(default="bge-m3")
    min_score: Mapped[float] = mapped_column(default=0.35)
    default_model_id: Mapped[UUID | None] = mapped_column(default=None)


class Role(UUIDPk, Base):
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("org_id", "key", name="uq_roles_org_key"),
        UniqueConstraint("org_id", "id", name="uq_roles_org_id"),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    key: Mapped[str]
    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    is_system: Mapped[bool] = mapped_column(default=False)
    is_assignable: Mapped[bool] = mapped_column(default=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (
        CheckConstraint(
            f"permission IN ({_PERMISSION_CHECK_VALUES})",
            name="ck_role_permissions_permission",
        ),
    )

    role_id: Mapped[UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission: Mapped[str] = mapped_column(primary_key=True)


class UserRoleBinding(UUIDPk, Base):
    __tablename__ = "user_role_bindings"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", "workspace_id", name="uq_user_role_scope"),
        Index(
            "uq_user_role_org_scope",
            "org_id",
            "user_id",
            "role_id",
            unique=True,
            postgresql_where=text("workspace_id IS NULL"),
        ),
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            name="fk_user_role_bindings_org_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "role_id"],
            ["roles.org_id", "roles.id"],
            name="fk_user_role_bindings_org_role",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_user_role_bindings_org_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["users.org_id", "users.id"],
            name="fk_user_role_bindings_org_creator",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[UUID] = mapped_column(index=True)
    role_id: Mapped[UUID] = mapped_column(index=True)
    workspace_id: Mapped[UUID | None] = mapped_column(default=None)
    created_by: Mapped[UUID | None] = mapped_column(default=None)


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_workspace_members_org_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            name="fk_workspace_members_org_user",
            ondelete="CASCADE",
        ),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
