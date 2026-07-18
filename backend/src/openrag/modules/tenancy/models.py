from uuid import UUID

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk


class Organization(UUIDPk, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(unique=True)


class Workspace(UUIDPk, Base):
    __tablename__ = "workspaces"

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
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_roles_org_key"),)

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    key: Mapped[str]
    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    is_system: Mapped[bool] = mapped_column(default=False)
    is_assignable: Mapped[bool] = mapped_column(default=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission: Mapped[str] = mapped_column(primary_key=True)


class UserRoleBinding(UUIDPk, Base):
    __tablename__ = "user_role_bindings"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", "workspace_id", name="uq_user_role_scope"),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    role_id: Mapped[UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"),
        index=True,
    )
    workspace_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        default=None,
    )
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"),
        default=None,
    )


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id"),
        primary_key=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"),
        primary_key=True,
    )
