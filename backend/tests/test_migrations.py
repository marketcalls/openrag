import runpy
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect, text

from openrag.core.config import get_settings

PRE_RBAC_REVISION = "4f2e1c9a7b30"
RBAC_REVISION = "6c4a2f8b9d10"
BACKEND_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_PERMISSIONS = {
    "administrator": {
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
    },
    "hse_manager": {
        "chat.use",
        "document.approve",
        "document.read",
        "document.upload",
    },
    "engineer": {"chat.use", "document.read", "document.upload"},
    "user": {"chat.use", "document.read"},
}


@pytest.fixture
def legacy_database(
    pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Config, Engine]]:
    monkeypatch.setenv("OPENRAG_DATABASE_URL", pg_url)
    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    engine = create_engine(pg_url.replace("+asyncpg", "+psycopg2"))
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
        connection.execute(
            text(
                "DO $$ BEGIN "
                "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'openrag') "
                "THEN CREATE ROLE openrag; END IF; "
                "END $$"
            )
        )

    command.upgrade(config, PRE_RBAC_REVISION)
    try:
        yield config, engine
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        engine.dispose()
        get_settings.cache_clear()


def insert_organization(connection: sa.Connection, *, org_id: UUID, name: str) -> None:
    connection.execute(
        text(
            "INSERT INTO organizations (id, name, created_at) "
            "VALUES (:id, :name, :created_at)"
        ),
        {"id": org_id, "name": name, "created_at": datetime(2026, 7, 19)},
    )


def insert_user(
    connection: sa.Connection,
    *,
    org_id: UUID,
    user_id: UUID,
    email: str,
    role: str,
) -> None:
    connection.execute(
        text(
            "INSERT INTO users "
            "(id, org_id, email, password_hash, role, active, created_at) "
            "VALUES (:id, :org_id, :email, :password_hash, :role, true, :created_at)"
        ),
        {
            "id": user_id,
            "org_id": org_id,
            "email": email,
            "password_hash": "inert-migration-test-hash",
            "role": role,
            "created_at": datetime(2026, 7, 19),
        },
    )


def test_capability_rbac_upgrade_locks_legacy_tables_during_backfill(
    legacy_database: tuple[Config, Engine],
) -> None:
    _, engine = legacy_database
    migration = runpy.run_path(
        str(
            BACKEND_ROOT
            / "migrations"
            / "versions"
            / "6c4a2f8b9d10_capability_rbac.py"
        )
    )
    lock_legacy_tables = cast(
        Callable[[sa.Connection], None], migration["_lock_legacy_tables"]
    )

    with engine.begin() as connection:
        lock_legacy_tables(connection)
        locks = connection.execute(
            text(
                "SELECT tables.relname, locks.mode FROM pg_locks AS locks "
                "JOIN pg_class AS tables ON tables.oid = locks.relation "
                "WHERE locks.pid = pg_backend_pid() "
                "AND tables.relname IN "
                "('organizations', 'users', 'invitations', 'workspaces', "
                "'workspace_members')"
            )
        ).mappings().all()

    assert {lock["relname"]: lock["mode"] for lock in locks} == {
        "organizations": "ShareRowExclusiveLock",
        "users": "ShareRowExclusiveLock",
        "invitations": "ShareRowExclusiveLock",
        "workspaces": "ShareRowExclusiveLock",
        "workspace_members": "ShareRowExclusiveLock",
    }


def test_capability_rbac_upgrade_backfills_and_round_trips_legacy_data(
    legacy_database: tuple[Config, Engine],
) -> None:
    config, engine = legacy_database
    assert ScriptDirectory.from_config(config).get_revision(RBAC_REVISION) is not None

    org_id = uuid4()
    superadmin_id = uuid4()
    admin_id = uuid4()
    user_id = uuid4()
    workspace_id = uuid4()
    invitation_id = uuid4()
    with engine.begin() as connection:
        insert_organization(connection, org_id=org_id, name="Migration Test Org")
        insert_user(
            connection,
            org_id=org_id,
            user_id=superadmin_id,
            email="root@example.com",
            role="superadmin",
        )
        insert_user(
            connection,
            org_id=org_id,
            user_id=admin_id,
            email="admin@example.com",
            role="admin",
        )
        insert_user(
            connection,
            org_id=org_id,
            user_id=user_id,
            email="user@example.com",
            role="user",
        )
        connection.execute(
            text(
                "INSERT INTO workspaces "
                "(id, org_id, name, embedding_model, min_score, created_at) "
                "VALUES (:id, :org_id, :name, :embedding_model, :min_score, :created_at)"
            ),
            {
                "id": workspace_id,
                "org_id": org_id,
                "name": "Legacy Workspace",
                "embedding_model": "bge-m3",
                "min_score": 0.35,
                "created_at": datetime(2026, 7, 19),
            },
        )
        connection.execute(
            text(
                "INSERT INTO workspace_members (workspace_id, user_id, role) "
                "VALUES (:workspace_id, :user_id, 'admin')"
            ),
            {"workspace_id": workspace_id, "user_id": admin_id},
        )
        connection.execute(
            text(
                "INSERT INTO invitations "
                "(id, org_id, email, role, token_hash, expires_at, created_at) "
                "VALUES "
                "(:id, :org_id, :email, 'admin', :token_hash, :expires_at, :created_at)"
            ),
            {
                "id": invitation_id,
                "org_id": org_id,
                "email": "invitee@example.com",
                "token_hash": "migration-test-token-hash",
                "expires_at": datetime(2027, 7, 19),
                "created_at": datetime(2026, 7, 19),
            },
        )

    command.upgrade(config, "head")

    inspector = inspect(engine)
    assert set(inspector.get_table_names()) >= {
        "roles",
        "role_permissions",
        "user_role_bindings",
    }
    assert {column["name"] for column in inspector.get_columns("workspace_members")} == {
        "org_id",
        "workspace_id",
        "user_id",
    }
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    assert "role" not in user_columns
    assert "is_platform_superadmin" in user_columns
    invitation_columns = {
        column["name"]: column for column in inspector.get_columns("invitations")
    }
    assert "role" not in invitation_columns
    assert invitation_columns["role_id"]["nullable"] is False

    with engine.connect() as connection:
        superadmin = connection.execute(
            text(
                "SELECT is_platform_superadmin FROM users WHERE id = :user_id"
            ),
            {"user_id": superadmin_id},
        ).mappings().one()
        assert superadmin["is_platform_superadmin"] is True

        roles = connection.execute(
            text(
                "SELECT key, name, description, is_system, is_assignable "
                "FROM roles WHERE org_id = :org_id"
            ),
            {"org_id": org_id},
        ).mappings().all()
        assert {role["key"] for role in roles} == set(EXPECTED_PERMISSIONS)
        assert all(role["is_system"] is True for role in roles)
        assert all(role["is_assignable"] is True for role in roles)

        permission_rows = connection.execute(
            text(
                "SELECT roles.key, role_permissions.permission "
                "FROM roles JOIN role_permissions ON role_permissions.role_id = roles.id "
                "WHERE roles.org_id = :org_id"
            ),
            {"org_id": org_id},
        ).mappings().all()
        actual_permissions = {
            key: {row["permission"] for row in permission_rows if row["key"] == key}
            for key in EXPECTED_PERMISSIONS
        }
        assert actual_permissions == EXPECTED_PERMISSIONS

        bindings = connection.execute(
            text(
                "SELECT users.email, roles.key AS role_key, bindings.workspace_id "
                "FROM user_role_bindings AS bindings "
                "JOIN users ON users.id = bindings.user_id "
                "JOIN roles ON roles.id = bindings.role_id "
                "ORDER BY users.email"
            )
        ).mappings().all()
        assert [dict(binding) for binding in bindings] == [
            {
                "email": "admin@example.com",
                "role_key": "administrator",
                "workspace_id": None,
            },
            {"email": "user@example.com", "role_key": "user", "workspace_id": None},
        ]

        invitation = connection.execute(
            text(
                "SELECT roles.key AS role_key FROM invitations "
                "JOIN roles ON (roles.org_id, roles.id) = "
                "(invitations.org_id, invitations.role_id) "
                "WHERE invitations.id = :invitation_id"
            ),
            {"invitation_id": invitation_id},
        ).mappings().one()
        assert invitation["role_key"] == "administrator"

        membership = connection.execute(
            text(
                "SELECT org_id, workspace_id, user_id FROM workspace_members "
                "WHERE workspace_id = :workspace_id AND user_id = :user_id"
            ),
            {"workspace_id": workspace_id, "user_id": admin_id},
        ).mappings().one()
        assert membership["org_id"] == org_id

    unique_constraints = {
        table: {constraint["name"] for constraint in inspector.get_unique_constraints(table)}
        for table in ("users", "workspaces", "roles", "user_role_bindings")
    }
    assert "uq_users_org_id" in unique_constraints["users"]
    assert "uq_workspaces_org_id" in unique_constraints["workspaces"]
    assert {"uq_roles_org_key", "uq_roles_org_id"} <= unique_constraints["roles"]
    assert "uq_user_role_scope" in unique_constraints["user_role_bindings"]

    binding_indexes = {
        index["name"]: index for index in inspector.get_indexes("user_role_bindings")
    }
    assert binding_indexes["uq_user_role_org_scope"]["unique"] is True
    assert "workspace_id IS NULL" in str(
        binding_indexes["uq_user_role_org_scope"]["dialect_options"][
            "postgresql_where"
        ]
    )
    assert {constraint["name"] for constraint in inspector.get_check_constraints(
        "role_permissions"
    )} == {"ck_role_permissions_permission"}

    expected_foreign_keys = {
        "fk_user_role_bindings_org_user",
        "fk_user_role_bindings_org_role",
        "fk_user_role_bindings_org_workspace",
        "fk_user_role_bindings_org_creator",
    }
    assert expected_foreign_keys <= {
        foreign_key["name"]
        for foreign_key in inspector.get_foreign_keys("user_role_bindings")
    }
    assert "fk_invitations_org_role" in {
        foreign_key["name"] for foreign_key in inspector.get_foreign_keys("invitations")
    }
    assert {
        "fk_workspace_members_org_workspace",
        "fk_workspace_members_org_user",
    } <= {
        foreign_key["name"]
        for foreign_key in inspector.get_foreign_keys("workspace_members")
    }

    command.downgrade(config, PRE_RBAC_REVISION)
    downgraded_inspector = inspect(engine)
    assert not {
        "roles",
        "role_permissions",
        "user_role_bindings",
    } & set(downgraded_inspector.get_table_names())
    assert "role" in {
        column["name"] for column in downgraded_inspector.get_columns("users")
    }
    assert "is_platform_superadmin" not in {
        column["name"] for column in downgraded_inspector.get_columns("users")
    }
    assert {column["name"] for column in downgraded_inspector.get_columns(
        "workspace_members"
    )} == {"workspace_id", "user_id", "role"}

    with engine.connect() as connection:
        downgraded_users = connection.execute(
            text("SELECT email, role FROM users ORDER BY email")
        ).mappings().all()
        assert [dict(user) for user in downgraded_users] == [
            {"email": "admin@example.com", "role": "admin"},
            {"email": "root@example.com", "role": "superadmin"},
            {"email": "user@example.com", "role": "user"},
        ]
        assert connection.execute(
            text("SELECT role FROM invitations WHERE id = :invitation_id"),
            {"invitation_id": invitation_id},
        ).scalar_one() == "admin"
        assert connection.execute(
            text(
                "SELECT role FROM workspace_members "
                "WHERE workspace_id = :workspace_id AND user_id = :user_id"
            ),
            {"workspace_id": workspace_id, "user_id": admin_id},
        ).scalar_one() == "user"

    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM roles WHERE org_id = :org_id"),
            {"org_id": org_id},
        ).scalar_one() == 4
        assert connection.execute(
            text("SELECT is_platform_superadmin FROM users WHERE id = :user_id"),
            {"user_id": superadmin_id},
        ).scalar_one() is True
        assert connection.execute(
            text(
                "SELECT array_agg(roles.key ORDER BY users.email) "
                "FROM user_role_bindings AS bindings "
                "JOIN users ON users.id = bindings.user_id "
                "JOIN roles ON roles.id = bindings.role_id"
            )
        ).scalar_one() == ["administrator", "user"]
        assert connection.execute(
            text(
                "SELECT roles.key FROM invitations JOIN roles "
                "ON (roles.org_id, roles.id) = "
                "(invitations.org_id, invitations.role_id) "
                "WHERE invitations.id = :invitation_id"
            ),
            {"invitation_id": invitation_id},
        ).scalar_one() == "administrator"
        assert connection.execute(
            text(
                "SELECT count(*) FROM workspace_members "
                "WHERE org_id = :org_id AND workspace_id = :workspace_id "
                "AND user_id = :user_id"
            ),
            {"org_id": org_id, "workspace_id": workspace_id, "user_id": admin_id},
        ).scalar_one() == 1


def test_capability_rbac_upgrade_rejects_an_unknown_user_role(
    legacy_database: tuple[Config, Engine],
) -> None:
    config, engine = legacy_database
    org_id = uuid4()
    with engine.begin() as connection:
        insert_organization(connection, org_id=org_id, name="Unknown User Role Org")
        insert_user(
            connection,
            org_id=org_id,
            user_id=uuid4(),
            email="owner@example.com",
            role="owner",
        )

    with pytest.raises(RuntimeError, match="unexpected legacy user roles.*owner"):
        command.upgrade(config, "head")


def test_capability_rbac_upgrade_rejects_an_unknown_invitation_role(
    legacy_database: tuple[Config, Engine],
) -> None:
    config, engine = legacy_database
    org_id = uuid4()
    with engine.begin() as connection:
        insert_organization(connection, org_id=org_id, name="Unknown Invite Role Org")
        connection.execute(
            text(
                "INSERT INTO invitations "
                "(id, org_id, email, role, token_hash, expires_at, created_at) "
                "VALUES "
                "(:id, :org_id, :email, 'owner', :token_hash, :expires_at, :created_at)"
            ),
            {
                "id": uuid4(),
                "org_id": org_id,
                "email": "owner-invite@example.com",
                "token_hash": "unknown-invitation-role-token",
                "expires_at": datetime(2027, 7, 19),
                "created_at": datetime(2026, 7, 19),
            },
        )

    with pytest.raises(RuntimeError, match="unexpected legacy invitation roles.*owner"):
        command.upgrade(config, "head")
