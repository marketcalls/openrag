"""capability rbac

Revision ID: 6c4a2f8b9d10
Revises: 4f2e1c9a7b30
Create Date: 2026-07-19 00:00:00.000000

"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import op

# This migration deliberately carries its own immutable template snapshot. Historical
# migrations must remain runnable even if application templates change later.
ROLE_TEMPLATES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "administrator",
        "Administrator",
        "Manage organization users, roles, workspaces and knowledge.",
        (
            "audit.read",
            "chat.use",
            "document.approve",
            "document.read",
            "document.upload",
            "model.configure",
            "rag.evaluate",
            "role.manage",
            "user.manage",
            "workspace.manage",
            "workspace.read_all",
        ),
    ),
    (
        "hse_manager",
        "HSE Manager",
        "Manage and approve HSE knowledge in assigned workspaces.",
        ("chat.use", "document.approve", "document.read", "document.upload"),
    ),
    (
        "engineer",
        "Engineer",
        "Use chat and contribute knowledge in assigned workspaces.",
        ("chat.use", "document.read", "document.upload"),
    ),
    (
        "user",
        "User",
        "Use grounded chat and read assigned workspace knowledge.",
        ("chat.use", "document.read"),
    ),
)

ALL_PERMISSIONS = tuple(
    sorted({permission for template in ROLE_TEMPLATES for permission in template[3]})
)
PERMISSION_CHECK_VALUES = ", ".join(f"'{permission}'" for permission in ALL_PERMISSIONS)

# revision identifiers, used by Alembic.
revision: str = "6c4a2f8b9d10"
down_revision: str | Sequence[str] | None = "4f2e1c9a7b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _lock_legacy_tables(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            "LOCK TABLE organizations, users, invitations, workspaces, "
            "workspace_members IN SHARE ROW EXCLUSIVE MODE"
        )
    )


def _unexpected_roles(
    connection: sa.Connection,
    *,
    table_name: str,
    accepted_roles: tuple[str, ...],
) -> list[str]:
    accepted = ", ".join(f"'{role}'" for role in accepted_roles)
    return list(
        connection.execute(
            sa.text(
                f"SELECT DISTINCT role FROM {table_name} "  # noqa: S608
                f"WHERE role NOT IN ({accepted}) ORDER BY role"
            )
        ).scalars()
    )


def _validate_legacy_roles(connection: sa.Connection) -> None:
    unexpected_user_roles = _unexpected_roles(
        connection,
        table_name="users",
        accepted_roles=("superadmin", "admin", "user"),
    )
    if unexpected_user_roles:
        raise RuntimeError(
            "unexpected legacy user roles: " + ", ".join(unexpected_user_roles)
        )

    unexpected_invitation_roles = _unexpected_roles(
        connection,
        table_name="invitations",
        accepted_roles=("admin", "user"),
    )
    if unexpected_invitation_roles:
        raise RuntimeError(
            "unexpected legacy invitation roles: "
            + ", ".join(unexpected_invitation_roles)
        )


def _seed_roles(connection: sa.Connection) -> dict[tuple[UUID, str], UUID]:
    roles = sa.table(
        "roles",
        sa.column("id", sa.Uuid()),
        sa.column("org_id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("is_system", sa.Boolean()),
        sa.column("is_assignable", sa.Boolean()),
        sa.column("created_at", sa.DateTime()),
    )
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Uuid()),
        sa.column("permission", sa.String()),
    )
    role_ids: dict[tuple[UUID, str], UUID] = {}
    role_rows: list[dict[str, object]] = []
    permission_rows: list[dict[str, object]] = []
    timestamp = _now()

    organization_ids = connection.execute(
        sa.text("SELECT id FROM organizations ORDER BY id")
    ).scalars()
    for org_id in organization_ids:
        for key, name, description, permissions in ROLE_TEMPLATES:
            role_id = uuid4()
            role_ids[(org_id, key)] = role_id
            role_rows.append(
                {
                    "id": role_id,
                    "org_id": org_id,
                    "key": key,
                    "name": name,
                    "description": description,
                    "is_system": True,
                    "is_assignable": True,
                    "created_at": timestamp,
                }
            )
            permission_rows.extend(
                {"role_id": role_id, "permission": permission}
                for permission in permissions
            )

    if role_rows:
        connection.execute(sa.insert(roles), role_rows)
        connection.execute(sa.insert(role_permissions), permission_rows)
    return role_ids


def _backfill_users(
    connection: sa.Connection,
    role_ids: dict[tuple[UUID, str], UUID],
) -> None:
    connection.execute(
        sa.text(
            "UPDATE users SET is_platform_superadmin = true WHERE role = 'superadmin'"
        )
    )
    bindings = sa.table(
        "user_role_bindings",
        sa.column("id", sa.Uuid()),
        sa.column("org_id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
        sa.column("role_id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("created_by", sa.Uuid()),
        sa.column("created_at", sa.DateTime()),
    )
    rows: list[dict[str, object]] = []
    timestamp = _now()
    legacy_users = connection.execute(
        sa.text(
            "SELECT id, org_id, role FROM users "
            "WHERE role IN ('admin', 'user') ORDER BY id"
        )
    ).mappings()
    for user in legacy_users:
        role_key = "administrator" if user["role"] == "admin" else "user"
        rows.append(
            {
                "id": uuid4(),
                "org_id": user["org_id"],
                "user_id": user["id"],
                "role_id": role_ids[(user["org_id"], role_key)],
                "workspace_id": None,
                "created_by": None,
                "created_at": timestamp,
            }
        )
    if rows:
        connection.execute(sa.insert(bindings), rows)


def _backfill_invitations(
    connection: sa.Connection,
    role_ids: dict[tuple[UUID, str], UUID],
) -> None:
    invitations = connection.execute(
        sa.text("SELECT id, org_id, role FROM invitations ORDER BY id")
    ).mappings()
    for invitation in invitations:
        role_key = "administrator" if invitation["role"] == "admin" else "user"
        connection.execute(
            sa.text(
                "UPDATE invitations SET role_id = :role_id "
                "WHERE id = :invitation_id"
            ),
            {
                "role_id": role_ids[(invitation["org_id"], role_key)],
                "invitation_id": invitation["id"],
            },
        )


def upgrade() -> None:
    """Replace legacy string roles with tenant-scoped capability roles."""
    connection = op.get_bind()
    _lock_legacy_tables(connection)
    _validate_legacy_roles(connection)

    op.add_column(
        "users",
        sa.Column(
            "is_platform_superadmin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_unique_constraint("uq_users_org_id", "users", ["org_id", "id"])
    op.create_unique_constraint(
        "uq_workspaces_org_id", "workspaces", ["org_id", "id"]
    )

    op.create_table(
        "roles",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("is_assignable", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "key", name="uq_roles_org_key"),
        sa.UniqueConstraint("org_id", "id", name="uq_roles_org_id"),
    )
    op.create_index(op.f("ix_roles_org_id"), "roles", ["org_id"], unique=False)
    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission", sa.String(), nullable=False),
        sa.CheckConstraint(
            f"permission IN ({PERMISSION_CHECK_VALUES})",
            name="ck_role_permissions_permission",
        ),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_id", "permission"),
    )
    op.create_table(
        "user_role_bindings",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["users.org_id", "users.id"],
            name="fk_user_role_bindings_org_creator",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "role_id"],
            ["roles.org_id", "roles.id"],
            name="fk_user_role_bindings_org_role",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            name="fk_user_role_bindings_org_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "workspace_id"],
            ["workspaces.org_id", "workspaces.id"],
            name="fk_user_role_bindings_org_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "role_id", "workspace_id", name="uq_user_role_scope"
        ),
    )
    op.create_index(
        op.f("ix_user_role_bindings_org_id"),
        "user_role_bindings",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_role_bindings_role_id"),
        "user_role_bindings",
        ["role_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_role_bindings_user_id"),
        "user_role_bindings",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "uq_user_role_org_scope",
        "user_role_bindings",
        ["org_id", "user_id", "role_id"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NULL"),
    )

    op.add_column("invitations", sa.Column("role_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_invitations_org_role",
        "invitations",
        "roles",
        ["org_id", "role_id"],
        ["org_id", "id"],
    )

    op.add_column("workspace_members", sa.Column("org_id", sa.Uuid(), nullable=True))
    connection.execute(
        sa.text(
            "UPDATE workspace_members AS members SET org_id = workspaces.org_id "
            "FROM workspaces WHERE workspaces.id = members.workspace_id"
        )
    )
    op.alter_column("workspace_members", "org_id", nullable=False)
    op.drop_constraint(
        "workspace_members_user_id_fkey", "workspace_members", type_="foreignkey"
    )
    op.drop_constraint(
        "workspace_members_workspace_id_fkey",
        "workspace_members",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "workspace_members_org_id_fkey",
        "workspace_members",
        "organizations",
        ["org_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_workspace_members_org_workspace",
        "workspace_members",
        "workspaces",
        ["org_id", "workspace_id"],
        ["org_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_workspace_members_org_user",
        "workspace_members",
        "users",
        ["org_id", "user_id"],
        ["org_id", "id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_workspace_members_org_id"),
        "workspace_members",
        ["org_id"],
        unique=False,
    )

    role_ids = _seed_roles(connection)
    _backfill_users(connection, role_ids)
    _backfill_invitations(connection, role_ids)

    op.alter_column("invitations", "role_id", nullable=False)
    op.drop_column("users", "role")
    op.drop_column("invitations", "role")
    op.drop_column("workspace_members", "role")


def downgrade() -> None:
    """Recreate the least-privileged legacy role representation."""
    connection = op.get_bind()

    op.add_column("users", sa.Column("role", sa.String(), nullable=True))
    connection.execute(
        sa.text(
            "UPDATE users AS users SET role = CASE "
            "WHEN users.is_platform_superadmin THEN 'superadmin' "
            "WHEN EXISTS ("
            "SELECT 1 FROM user_role_bindings AS bindings "
            "JOIN roles ON roles.id = bindings.role_id "
            "WHERE bindings.org_id = users.org_id "
            "AND bindings.user_id = users.id "
            "AND bindings.workspace_id IS NULL "
            "AND roles.key = 'administrator'"
            ") THEN 'admin' ELSE 'user' END"
        )
    )
    op.alter_column("users", "role", nullable=False)

    op.add_column("invitations", sa.Column("role", sa.String(), nullable=True))
    connection.execute(
        sa.text(
            "UPDATE invitations AS invitations SET role = CASE "
            "WHEN roles.key = 'administrator' THEN 'admin' ELSE 'user' END "
            "FROM roles WHERE roles.org_id = invitations.org_id "
            "AND roles.id = invitations.role_id"
        )
    )
    op.alter_column("invitations", "role", nullable=False)

    op.add_column("workspace_members", sa.Column("role", sa.String(), nullable=True))
    connection.execute(sa.text("UPDATE workspace_members SET role = 'user'"))
    op.alter_column("workspace_members", "role", nullable=False)

    op.drop_constraint("fk_invitations_org_role", "invitations", type_="foreignkey")
    op.drop_column("invitations", "role_id")

    op.drop_index(op.f("ix_workspace_members_org_id"), table_name="workspace_members")
    op.drop_constraint(
        "fk_workspace_members_org_user", "workspace_members", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_workspace_members_org_workspace",
        "workspace_members",
        type_="foreignkey",
    )
    op.drop_constraint(
        "workspace_members_org_id_fkey", "workspace_members", type_="foreignkey"
    )
    op.drop_column("workspace_members", "org_id")
    op.create_foreign_key(
        "workspace_members_user_id_fkey",
        "workspace_members",
        "users",
        ["user_id"],
        ["id"],
    )
    op.create_foreign_key(
        "workspace_members_workspace_id_fkey",
        "workspace_members",
        "workspaces",
        ["workspace_id"],
        ["id"],
    )

    op.drop_index("uq_user_role_org_scope", table_name="user_role_bindings")
    op.drop_index(
        op.f("ix_user_role_bindings_user_id"), table_name="user_role_bindings"
    )
    op.drop_index(
        op.f("ix_user_role_bindings_role_id"), table_name="user_role_bindings"
    )
    op.drop_index(
        op.f("ix_user_role_bindings_org_id"), table_name="user_role_bindings"
    )
    op.drop_table("user_role_bindings")
    op.drop_table("role_permissions")
    op.drop_index(op.f("ix_roles_org_id"), table_name="roles")
    op.drop_table("roles")

    op.drop_constraint("uq_workspaces_org_id", "workspaces", type_="unique")
    op.drop_constraint("uq_users_org_id", "users", type_="unique")
    op.drop_column("users", "is_platform_superadmin")
