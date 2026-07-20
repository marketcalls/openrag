import json
import runpy
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
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
AUTHORITY_REVISION = "9d2c7a4e1f60"
DELETION_REVISION = "4b8e0f7a3c21"
OUTBOX_REVISION = "a4f87e62b913"
STAGE_REVISION = "d8a4f2c91e37"
REBUILD_REVISION = "f1c3e8a2b7d4"
RUNTIME_REVISION = "a2d4f6b8c0e1"
EMBEDDING_PROFILE_REVISION = "b3e5f7a9c1d2"
EMBEDDING_DEPLOYMENT_REVISION = "c4f6a8b0d2e3"
REINDEX_STAGE_REVISION = "d5a7c9e1f3b4"
EMBEDDING_LEASE_REVISION = "e6b8d0f2a4c5"
RAG_OPERATIONS_REVISION = "c8e0a3b5d7f9"
RAG_EVALUATIONS_REVISION = "d9f1b4c6e8a0"
OPERATIONS_INDEX_REVISION = "e2a4c6d8f0b1"
REASONING_EFFORT_REVISION = "f3b5d7e9a1c2"
LITELLM_LIBRARY_REVISION = "a4c6e8f0b2d3"
BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def pre_outbox_database(
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
    command.upgrade(config, DELETION_REVISION)
    try:
        yield config, engine
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        engine.dispose()
        get_settings.cache_clear()


def insert_legacy_outbox(
    connection: sa.Connection,
    *,
    attempts: int = 2,
    event_type: str = "document.version.lifecycle.v1",
    aggregate_type: str = "document_version",
    dedupe_key: str | None = None,
    lease_owner: str | None = None,
    lease_expires_at: datetime | None = None,
    published_at: datetime | None = None,
    last_error: str | None = None,
    payload: object | None = None,
) -> UUID:
    row_id = uuid4()
    event_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO outbox_events "
            "(id,event_id,aggregate_type,aggregate_id,event_type,payload,dedupe_key,"
            "attempts,lease_owner,lease_expires_at,published_at,last_error,created_at) "
            "VALUES (:id,:event_id,:aggregate_type,:aggregate_id,:event_type,"
            "CAST(:payload AS json),:dedupe_key,:attempts,:lease_owner,:lease_expires_at,"
            ":published_at,:last_error,:created_at)"
        ),
        {
            "id": row_id,
            "event_id": event_id,
            "aggregate_type": aggregate_type,
            "aggregate_id": uuid4(),
            "event_type": event_type,
            "payload": json.dumps(payload if payload is not None else {"state": "review"}),
            "dedupe_key": dedupe_key or f"legacy:{event_id}",
            "attempts": attempts,
            "lease_owner": lease_owner,
            "lease_expires_at": lease_expires_at,
            "published_at": published_at,
            "last_error": last_error,
            "created_at": datetime(2026, 7, 19),
        },
    )
    return row_id


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
        text("INSERT INTO organizations (id, name, created_at) VALUES (:id, :name, :created_at)"),
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


def insert_nonlegacy_version(
    connection: sa.Connection,
    ids: SimpleNamespace,
    *,
    state: str,
    provenance_state: str,
    document_id: UUID | None = None,
    version_id: UUID | None = None,
) -> tuple[UUID, UUID]:
    now = datetime(2026, 7, 19, 1)
    document_id = document_id or uuid4()
    version_id = version_id or uuid4()
    connection.execute(
        text(
            "INSERT INTO documents "
            "(id,org_id,workspace_id,name,created_by,updated_at,created_at) "
            "VALUES (:document,:org,:workspace,'Versioned',:actor,:now,:now)"
        ),
        {
            "document": document_id,
            "org": ids.org_id,
            "workspace": ids.workspace_id,
            "actor": ids.user_id,
            "now": now,
        },
    )
    connection.execute(
        text(
            "INSERT INTO document_versions "
            "(id,org_id,workspace_id,document_id,sequence,version_label,version_key,"
            "content_hash,source_filename,source_mime,source_size_bytes,"
            "source_storage_key,source_page_count,parser_profile_version,"
            "ocr_profile_version,chunking_profile_version,embedding_profile_version,"
            "index_profile_version,state,provenance_state,lifecycle_revision,"
            "created_by,updated_at,created_at) VALUES "
            "(:id,:org,:workspace,:document,1,'Rev 1','rev 1',:hash,'source.pdf',"
            "'application/pdf',1,:key,:pages,'docling/v1','none/v1','semantic/v1',"
            "'bge-m3/v1','hybrid/v1',:state,:provenance,1,:actor,:now,:now)"
        ),
        {
            "id": version_id,
            "org": ids.org_id,
            "workspace": ids.workspace_id,
            "document": document_id,
            "hash": version_id.hex * 2,
            "key": f"versions/{version_id}/source",
            "pages": None if state in {"draft", "processing", "failed"} else 1,
            "state": state,
            "provenance": provenance_state,
            "actor": ids.user_id,
            "now": now,
        },
    )
    return document_id, version_id


def insert_document_block(
    connection: sa.Connection,
    ids: SimpleNamespace,
    version_id: UUID,
) -> UUID:
    block_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO document_blocks "
            "(id,org_id,document_version_id,parent_block_id,ordinal,text,page_number,"
            "locator_kind,locator_label,block_type,section_path,source_coordinates,"
            "extraction_method,ocr_profile_version,ocr_confidence,content_hash,created_at) "
            "VALUES (:id,:org,:version,NULL,0,'evidence',1,'page','1','paragraph',"
            "CAST('[\"Scope\"]' AS jsonb),NULL,'parser','none/v1',NULL,:hash,:now)"
        ),
        {
            "id": block_id,
            "org": ids.org_id,
            "version": version_id,
            "hash": block_id.hex * 2,
            "now": datetime(2026, 7, 19, 1),
        },
    )
    return block_id


def test_capability_rbac_upgrade_locks_legacy_tables_during_backfill(
    legacy_database: tuple[Config, Engine],
) -> None:
    _, engine = legacy_database
    migration = runpy.run_path(
        str(BACKEND_ROOT / "migrations" / "versions" / "6c4a2f8b9d10_capability_rbac.py")
    )
    lock_legacy_tables = cast(Callable[[sa.Connection], None], migration["_lock_legacy_tables"])

    with engine.begin() as connection:
        lock_legacy_tables(connection)
        locks = (
            connection.execute(
                text(
                    "SELECT tables.relname, locks.mode FROM pg_locks AS locks "
                    "JOIN pg_class AS tables ON tables.oid = locks.relation "
                    "WHERE locks.pid = pg_backend_pid() "
                    "AND tables.relname IN "
                    "('organizations', 'users', 'invitations', 'workspaces', "
                    "'workspace_members')"
                )
            )
            .mappings()
            .all()
        )

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
    invitation_columns = {column["name"]: column for column in inspector.get_columns("invitations")}
    assert "role" not in invitation_columns
    assert invitation_columns["role_id"]["nullable"] is False

    with engine.connect() as connection:
        superadmin = (
            connection.execute(
                text("SELECT is_platform_superadmin FROM users WHERE id = :user_id"),
                {"user_id": superadmin_id},
            )
            .mappings()
            .one()
        )
        assert superadmin["is_platform_superadmin"] is True

        roles = (
            connection.execute(
                text(
                    "SELECT key, name, description, is_system, is_assignable "
                    "FROM roles WHERE org_id = :org_id"
                ),
                {"org_id": org_id},
            )
            .mappings()
            .all()
        )
        assert {role["key"] for role in roles} == set(EXPECTED_PERMISSIONS)
        assert all(role["is_system"] is True for role in roles)
        assert all(role["is_assignable"] is True for role in roles)

        permission_rows = (
            connection.execute(
                text(
                    "SELECT roles.key, role_permissions.permission "
                    "FROM roles JOIN role_permissions ON role_permissions.role_id = roles.id "
                    "WHERE roles.org_id = :org_id"
                ),
                {"org_id": org_id},
            )
            .mappings()
            .all()
        )
        actual_permissions = {
            key: {row["permission"] for row in permission_rows if row["key"] == key}
            for key in EXPECTED_PERMISSIONS
        }
        assert actual_permissions == EXPECTED_PERMISSIONS

        bindings = (
            connection.execute(
                text(
                    "SELECT users.email, roles.key AS role_key, bindings.workspace_id "
                    "FROM user_role_bindings AS bindings "
                    "JOIN users ON users.id = bindings.user_id "
                    "JOIN roles ON roles.id = bindings.role_id "
                    "ORDER BY users.email"
                )
            )
            .mappings()
            .all()
        )
        assert [dict(binding) for binding in bindings] == [
            {
                "email": "admin@example.com",
                "role_key": "administrator",
                "workspace_id": None,
            },
            {"email": "user@example.com", "role_key": "user", "workspace_id": None},
        ]

        invitation = (
            connection.execute(
                text(
                    "SELECT roles.key AS role_key FROM invitations "
                    "JOIN roles ON (roles.org_id, roles.id) = "
                    "(invitations.org_id, invitations.role_id) "
                    "WHERE invitations.id = :invitation_id"
                ),
                {"invitation_id": invitation_id},
            )
            .mappings()
            .one()
        )
        assert invitation["role_key"] == "administrator"

        membership = (
            connection.execute(
                text(
                    "SELECT org_id, workspace_id, user_id FROM workspace_members "
                    "WHERE workspace_id = :workspace_id AND user_id = :user_id"
                ),
                {"workspace_id": workspace_id, "user_id": admin_id},
            )
            .mappings()
            .one()
        )
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
        binding_indexes["uq_user_role_org_scope"]["dialect_options"]["postgresql_where"]
    )
    assert {
        constraint["name"] for constraint in inspector.get_check_constraints("role_permissions")
    } == {"ck_role_permissions_permission"}

    expected_foreign_keys = {
        "fk_user_role_bindings_org_user",
        "fk_user_role_bindings_org_role",
        "fk_user_role_bindings_org_workspace",
        "fk_user_role_bindings_org_creator",
    }
    assert expected_foreign_keys <= {
        foreign_key["name"] for foreign_key in inspector.get_foreign_keys("user_role_bindings")
    }
    assert "fk_invitations_org_role" in {
        foreign_key["name"] for foreign_key in inspector.get_foreign_keys("invitations")
    }
    assert {
        "fk_workspace_members_org_workspace",
        "fk_workspace_members_org_user",
    } <= {foreign_key["name"] for foreign_key in inspector.get_foreign_keys("workspace_members")}

    command.downgrade(config, PRE_RBAC_REVISION)
    downgraded_inspector = inspect(engine)
    assert not {
        "roles",
        "role_permissions",
        "user_role_bindings",
    } & set(downgraded_inspector.get_table_names())
    assert "role" in {column["name"] for column in downgraded_inspector.get_columns("users")}
    assert "is_platform_superadmin" not in {
        column["name"] for column in downgraded_inspector.get_columns("users")
    }
    assert {column["name"] for column in downgraded_inspector.get_columns("workspace_members")} == {
        "workspace_id",
        "user_id",
        "role",
    }

    with engine.connect() as connection:
        downgraded_users = (
            connection.execute(text("SELECT email, role FROM users ORDER BY email"))
            .mappings()
            .all()
        )
        assert [dict(user) for user in downgraded_users] == [
            {"email": "admin@example.com", "role": "admin"},
            {"email": "root@example.com", "role": "superadmin"},
            {"email": "user@example.com", "role": "user"},
        ]
        assert (
            connection.execute(
                text("SELECT role FROM invitations WHERE id = :invitation_id"),
                {"invitation_id": invitation_id},
            ).scalar_one()
            == "admin"
        )
        assert (
            connection.execute(
                text(
                    "SELECT role FROM workspace_members "
                    "WHERE workspace_id = :workspace_id AND user_id = :user_id"
                ),
                {"workspace_id": workspace_id, "user_id": admin_id},
            ).scalar_one()
            == "user"
        )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM roles WHERE org_id = :org_id"),
                {"org_id": org_id},
            ).scalar_one()
            == 4
        )
        assert (
            connection.execute(
                text("SELECT is_platform_superadmin FROM users WHERE id = :user_id"),
                {"user_id": superadmin_id},
            ).scalar_one()
            is True
        )
        assert connection.execute(
            text(
                "SELECT array_agg(roles.key ORDER BY users.email) "
                "FROM user_role_bindings AS bindings "
                "JOIN users ON users.id = bindings.user_id "
                "JOIN roles ON roles.id = bindings.role_id"
            )
        ).scalar_one() == ["administrator", "user"]
        assert (
            connection.execute(
                text(
                    "SELECT roles.key FROM invitations JOIN roles "
                    "ON (roles.org_id, roles.id) = "
                    "(invitations.org_id, invitations.role_id) "
                    "WHERE invitations.id = :invitation_id"
                ),
                {"invitation_id": invitation_id},
            ).scalar_one()
            == "administrator"
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM workspace_members "
                    "WHERE org_id = :org_id AND workspace_id = :workspace_id "
                    "AND user_id = :user_id"
                ),
                {"org_id": org_id, "workspace_id": workspace_id, "user_id": admin_id},
            ).scalar_one()
            == 1
        )


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


@pytest.fixture
def authority_db(
    pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Config, Engine, SimpleNamespace]]:
    """A populated 6c4a database exercising every legacy status mapping."""

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
                "DO $$ BEGIN IF NOT EXISTS "
                "(SELECT 1 FROM pg_roles WHERE rolname = 'openrag') "
                "THEN CREATE ROLE openrag; END IF; END $$"
            )
        )
    command.upgrade(config, RBAC_REVISION)

    ids = SimpleNamespace(
        org_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        document_ids={status: uuid4() for status in ("indexed", "failed", "queued", "processing")},
    )
    with engine.begin() as connection:
        insert_organization(connection, org_id=ids.org_id, name="Authority Migration Org")
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, org_id, email, password_hash, active, is_platform_superadmin, created_at) "
                "VALUES (:id, :org_id, 'authority@example.com', 'inert', true, false, :now)"
            ),
            {"id": ids.user_id, "org_id": ids.org_id, "now": datetime(2026, 7, 19)},
        )
        connection.execute(
            text(
                "INSERT INTO workspaces "
                "(id, org_id, name, embedding_model, min_score, created_at) "
                "VALUES (:id, :org_id, 'Authority', 'bge-m3', 0.35, :now)"
            ),
            {"id": ids.workspace_id, "org_id": ids.org_id, "now": datetime(2026, 7, 19)},
        )
        for offset, (status, document_id) in enumerate(ids.document_ids.items(), start=1):
            connection.execute(
                text(
                    "INSERT INTO documents "
                    "(id, org_id, workspace_id, filename, mime, size_bytes, content_hash, "
                    "status, error, storage_key, page_count, created_by, updated_at, created_at) "
                    "VALUES (:id, :org_id, :workspace_id, :filename, 'application/pdf', "
                    ":size_bytes, :content_hash, :status, NULL, :storage_key, :page_count, "
                    ":created_by, :now, :now)"
                ),
                {
                    "id": document_id,
                    "org_id": ids.org_id,
                    "workspace_id": ids.workspace_id,
                    "filename": f"{status}.pdf",
                    "size_bytes": offset * 100,
                    "content_hash": f"{offset:064x}",
                    "status": status,
                    "storage_key": f"legacy/{status}.pdf",
                    "page_count": 7 if status == "indexed" else None,
                    "created_by": ids.user_id,
                    "now": datetime(2026, 7, 19),
                },
            )
    try:
        yield config, engine, ids
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        engine.dispose()
        get_settings.cache_clear()


def test_migration_graph_has_one_current_head(
    authority_db: tuple[Config, Engine, object],
) -> None:
    config, _engine, _ids = authority_db
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == [LITELLM_LIBRARY_REVISION]
    assert script.get_revision(AUTHORITY_REVISION).down_revision == RBAC_REVISION
    assert script.get_revision(DELETION_REVISION).down_revision == AUTHORITY_REVISION
    assert script.get_revision(STAGE_REVISION).down_revision == OUTBOX_REVISION
    assert script.get_revision(REBUILD_REVISION).down_revision == STAGE_REVISION
    assert script.get_revision(RUNTIME_REVISION).down_revision == REBUILD_REVISION
    assert script.get_revision(EMBEDDING_PROFILE_REVISION).down_revision == RUNTIME_REVISION
    assert (
        script.get_revision(EMBEDDING_DEPLOYMENT_REVISION).down_revision
        == EMBEDDING_PROFILE_REVISION
    )
    assert (
        script.get_revision(REINDEX_STAGE_REVISION).down_revision
        == EMBEDDING_DEPLOYMENT_REVISION
    )
    assert (
        script.get_revision(EMBEDDING_LEASE_REVISION).down_revision
        == REINDEX_STAGE_REVISION
    )
    assert script.get_revision(RAG_EVALUATIONS_REVISION).down_revision == RAG_OPERATIONS_REVISION
    assert script.get_revision(OPERATIONS_INDEX_REVISION).down_revision == RAG_EVALUATIONS_REVISION
    assert script.get_revision(REASONING_EFFORT_REVISION).down_revision == OPERATIONS_INDEX_REVISION


def test_deletion_upgrade_adds_bounded_restartable_markers_and_closes_processing_delete(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, DELETION_REVISION)
    columns = {
        column["name"]: column for column in inspect(engine).get_columns("document_versions")
    }
    assert set(columns) >= {
        "source_delete_requested_at",
        "source_delete_requested_by",
        "source_deleted_at",
    }
    assert all(
        columns[name]["nullable"]
        for name in (
            "source_delete_requested_at",
            "source_delete_requested_by",
            "source_deleted_at",
        )
    )

    processing_id = ids.document_ids["processing"]
    failed_id = ids.document_ids["failed"]
    with engine.begin() as connection:
        with pytest.raises(sa.exc.DBAPIError, match="deletion request"):
            with connection.begin_nested():
                connection.execute(
                    text("DELETE FROM document_versions WHERE id=:id"),
                    {"id": processing_id},
                )

    with engine.begin() as connection:
        with pytest.raises(sa.exc.DBAPIError, match="deletion request"):
            with connection.begin_nested():
                connection.execute(
                    text("DELETE FROM document_versions WHERE id=:id"),
                    {"id": failed_id},
                )

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_versions SET "
                "source_delete_requested_at=:now, source_delete_requested_by=:actor "
                "WHERE id=:id"
            ),
            {
                "id": failed_id,
                "actor": ids.user_id,
                "now": datetime(2026, 7, 19, 1),
            },
        )
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE document_versions SET source_deleted_at=:now WHERE id=:id"),
            {"id": failed_id, "now": datetime(2026, 7, 19, 2)},
        )
        connection.execute(text("DELETE FROM document_versions WHERE id=:id"), {"id": failed_id})
        assert (
            connection.execute(
                text("SELECT count(*) FROM document_versions WHERE id=:id"),
                {"id": failed_id},
            ).scalar_one()
            == 0
        )


def test_deletion_upgrade_backfills_durable_legacy_approval_evidence(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    indexed_id = ids.document_ids["indexed"]
    with engine.connect() as connection:
        before = connection.execute(
            text(
                "SELECT approved_by, approved_at, decision_at FROM document_versions WHERE id=:id"
            ),
            {"id": indexed_id},
        ).one()
    assert before == (None, None, None)

    command.upgrade(config, DELETION_REVISION)

    with engine.connect() as connection:
        approved_by, approved_at, decision_at, created_by = connection.execute(
            text(
                "SELECT approved_by, approved_at, decision_at, created_by "
                "FROM document_versions WHERE id=:id"
            ),
            {"id": indexed_id},
        ).one()
    assert approved_by == created_by == ids.user_id
    assert approved_at is not None
    assert decision_at == approved_at
    with pytest.raises(RuntimeError, match="backfilled approval evidence exists"):
        command.downgrade(config, AUTHORITY_REVISION)


def test_deletion_trigger_rejects_old_worker_lifecycle_transitions(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, DELETION_REVISION)
    processing_id = ids.document_ids["processing"]
    with engine.begin() as connection:
        with pytest.raises(sa.exc.DBAPIError, match="lifecycle revision"):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "UPDATE document_versions SET state='approved', "
                        "provenance_state='legacy_pending', source_page_count=1 "
                        "WHERE id=:id"
                    ),
                    {"id": processing_id},
                )
        connection.execute(
            text(
                "UPDATE document_versions SET state='approved', "
                "provenance_state='legacy_pending', source_page_count=1, "
                "lifecycle_revision=lifecycle_revision+1, approved_by=:actor, "
                "approved_at=now(), decision_at=now() WHERE id=:id"
            ),
            {"id": processing_id, "actor": ids.user_id},
        )
        with pytest.raises(sa.exc.DBAPIError, match="lifecycle revision"):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "UPDATE document_versions SET state='failed', "
                        "provenance_state='none' WHERE id=:id"
                    ),
                    {"id": processing_id},
                )


def test_deletion_upgrade_requires_ordered_complete_marker_and_preserves_decision_history(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, DELETION_REVISION)
    with engine.begin() as connection:
        document_id, rejected_id = insert_nonlegacy_version(
            connection,
            ids,
            state="rejected",
            provenance_state="ready",
        )
    with engine.begin() as connection:
        with pytest.raises(sa.exc.DBAPIError):
            with connection.begin_nested():
                connection.execute(
                    text("UPDATE document_versions SET source_deleted_at=:now WHERE id=:id"),
                    {"id": rejected_id, "now": datetime(2026, 7, 19, 1)},
                )

    # A restrictive governance FK and append-only trigger retain a rejected
    # metadata tombstone even after external bytes/provenance are purged.
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO document_version_decision_records "
                "(id,org_id,workspace_id,document_id,document_version_id,"
                "lifecycle_revision,decision,actor_id,reason,created_at) "
                "VALUES (:row_id,:org,:workspace,:document,:version,1,'rejected',"
                ":actor,'governed',:now)"
            ),
            {
                "row_id": uuid4(),
                "org": ids.org_id,
                "workspace": ids.workspace_id,
                "document": document_id,
                "version": rejected_id,
                "actor": ids.user_id,
                "now": datetime(2026, 7, 19, 1),
            },
        )
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_versions SET source_delete_requested_at=:now, "
                "source_delete_requested_by=:actor WHERE id=:id"
            ),
            {
                "id": rejected_id,
                "actor": ids.user_id,
                "now": datetime(2026, 7, 19, 1),
            },
        )
    with engine.begin() as connection:
        with pytest.raises(sa.exc.DBAPIError):
            with connection.begin_nested():
                connection.execute(
                    text("DELETE FROM document_versions WHERE id=:id"),
                    {"id": rejected_id},
                )

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE document_versions SET source_deleted_at=:now WHERE id=:id"),
            {"id": rejected_id, "now": datetime(2026, 7, 19, 2)},
        )
        assert (
            connection.execute(
                text("SELECT state, source_deleted_at FROM document_versions WHERE id=:id"),
                {"id": rejected_id},
            ).one()[0]
            == "rejected"
        )
        with pytest.raises(sa.exc.DBAPIError):
            with connection.begin_nested():
                connection.execute(
                    text("DELETE FROM document_versions WHERE id=:id"),
                    {"id": rejected_id},
                )


def test_deletion_upgrade_admits_only_explicit_never_approved_states(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, DELETION_REVISION)
    now = datetime(2026, 7, 19, 1)

    def insert_version(connection: sa.Connection, state: str) -> UUID:
        document_id = uuid4()
        version_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO documents "
                "(id,org_id,workspace_id,name,created_by,updated_at,created_at) "
                "VALUES (:document,:org,:workspace,:name,:actor,:now,:now)"
            ),
            {
                "document": document_id,
                "org": ids.org_id,
                "workspace": ids.workspace_id,
                "name": f"{state} document",
                "actor": ids.user_id,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_versions "
                "(id,org_id,workspace_id,document_id,sequence,version_label,version_key,"
                "content_hash,source_filename,source_mime,source_size_bytes,"
                "source_storage_key,source_page_count,parser_profile_version,"
                "ocr_profile_version,chunking_profile_version,embedding_profile_version,"
                "index_profile_version,state,provenance_state,lifecycle_revision,"
                "created_by,updated_at,created_at) VALUES "
                "(:id,:org,:workspace,:document,1,'Rev 1','rev 1',:hash,'source.pdf',"
                "'application/pdf',1,:key,:pages,'docling/v1','none/v1','semantic/v1',"
                "'bge-m3/v1','hybrid/v1',:state,:provenance,1,:actor,:now,:now)"
            ),
            {
                "id": version_id,
                "org": ids.org_id,
                "workspace": ids.workspace_id,
                "document": document_id,
                "hash": version_id.hex.ljust(64, "0"),
                "key": f"versions/{version_id}/source",
                "pages": None if state in {"draft", "processing", "failed"} else 1,
                "state": state,
                "provenance": (
                    "failed"
                    if state == "failed"
                    else "none"
                    if state in {"draft", "processing"}
                    else "ready"
                ),
                "actor": ids.user_id,
                "now": now,
            },
        )
        return version_id

    with engine.begin() as connection:
        version_ids = {
            state: insert_version(connection, state)
            for state in (
                "draft",
                "processing",
                "review",
                "approved",
                "rejected",
                "superseded",
                "obsolete",
                "failed",
            )
        }

    for state in ("draft", "rejected", "failed"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE document_versions SET source_delete_requested_at=:now, "
                    "source_delete_requested_by=:actor WHERE id=:id"
                ),
                {"now": now, "actor": ids.user_id, "id": version_ids[state]},
            )
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE document_versions SET source_deleted_at=:deleted_at WHERE id=:id"),
                {
                    "deleted_at": datetime(2026, 7, 19, 2),
                    "id": version_ids[state],
                },
            )
            connection.execute(
                text("DELETE FROM document_versions WHERE id=:id"),
                {"id": version_ids[state]},
            )

    for state in ("processing", "review", "approved", "superseded", "obsolete"):
        with engine.begin() as connection:
            with pytest.raises(sa.exc.DBAPIError):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE document_versions SET source_delete_requested_at=:now, "
                            "source_delete_requested_by=:actor WHERE id=:id"
                        ),
                        {"now": now, "actor": ids.user_id, "id": version_ids[state]},
                    )
            assert (
                connection.execute(
                    text("SELECT source_delete_requested_at FROM document_versions WHERE id=:id"),
                    {"id": version_ids[state]},
                ).scalar_one()
                is None
            )


def test_deletion_marker_actor_pair_and_time_order_are_database_enforced(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, DELETION_REVISION)
    failed_id = ids.document_ids["failed"]
    now = datetime(2026, 7, 19, 1)
    invalid_updates = (
        ("source_delete_requested_at=:now", {"now": now}),
        ("source_delete_requested_by=:actor", {"actor": ids.user_id}),
        (
            "source_delete_requested_at=:now, source_delete_requested_by=:actor, "
            "source_deleted_at=:before",
            {"now": now, "actor": ids.user_id, "before": datetime(2026, 7, 19)},
        ),
    )
    for assignment, params in invalid_updates:
        with engine.begin() as connection:
            with pytest.raises(sa.exc.DBAPIError):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            f"UPDATE document_versions SET {assignment} WHERE id=:id"  # noqa: S608 - closed test matrix
                        ),
                        {**params, "id": failed_id},
                    )


def test_evidence_artifact_mutation_requires_exact_processing_building_pair(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, DELETION_REVISION)
    with engine.begin() as connection:
        _document_id, allowed_id = insert_nonlegacy_version(
            connection,
            ids,
            state="processing",
            provenance_state="building",
        )
        denied_ids = [
            insert_nonlegacy_version(
                connection,
                ids,
                state=state,
                provenance_state=provenance,
            )[1]
            for state, provenance in (
                ("processing", "failed"),
                ("draft", "building"),
            )
        ]

    with engine.begin() as connection:
        insert_document_block(connection, ids, allowed_id)

    for version_id in denied_ids:
        with engine.begin() as connection:
            with pytest.raises(
                sa.exc.DBAPIError,
                match="processing/building owner",
            ):
                with connection.begin_nested():
                    insert_document_block(connection, ids, version_id)


def test_decision_insert_and_deletion_marker_serialize_in_both_race_orders(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, DELETION_REVISION)
    with engine.begin() as connection:
        first_document, marker_first_version = insert_nonlegacy_version(
            connection,
            ids,
            state="failed",
            provenance_state="failed",
        )
        second_document, decision_first_version = insert_nonlegacy_version(
            connection,
            ids,
            state="failed",
            provenance_state="failed",
        )

    def insert_approved_decision(
        version_id: UUID,
        document_id: UUID,
        started: Event,
    ) -> None:
        with engine.begin() as connection:
            started.set()
            connection.execute(
                text(
                    "INSERT INTO document_version_decision_records "
                    "(id,org_id,workspace_id,document_id,document_version_id,"
                    "lifecycle_revision,decision,actor_id,reason,created_at) VALUES "
                    "(:id,:org,:workspace,:document,:version,1,'approved',:actor,NULL,:now)"
                ),
                {
                    "id": uuid4(),
                    "org": ids.org_id,
                    "workspace": ids.workspace_id,
                    "document": document_id,
                    "version": version_id,
                    "actor": ids.user_id,
                    "now": datetime(2026, 7, 19, 1),
                },
            )

    def request_marker(version_id: UUID, started: Event) -> None:
        with engine.begin() as connection:
            started.set()
            connection.execute(
                text(
                    "UPDATE document_versions SET source_delete_requested_at=:now, "
                    "source_delete_requested_by=:actor WHERE id=:id"
                ),
                {
                    "id": version_id,
                    "actor": ids.user_id,
                    "now": datetime(2026, 7, 19, 1),
                },
            )

    marker_connection = engine.connect()
    marker_transaction = marker_connection.begin()
    try:
        marker_connection.execute(
            text(
                "UPDATE document_versions SET source_delete_requested_at=:now, "
                "source_delete_requested_by=:actor WHERE id=:id"
            ),
            {
                "id": marker_first_version,
                "actor": ids.user_id,
                "now": datetime(2026, 7, 19, 1),
            },
        )
        started = Event()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                insert_approved_decision,
                marker_first_version,
                first_document,
                started,
            )
            assert started.wait(timeout=5)
            marker_transaction.commit()
            with pytest.raises(sa.exc.DBAPIError, match="after deletion request"):
                future.result(timeout=10)
    finally:
        if marker_transaction.is_active:
            marker_transaction.rollback()
        marker_connection.close()

    decision_connection = engine.connect()
    decision_transaction = decision_connection.begin()
    try:
        started = Event()
        insert_approved_decision_sql = text(
            "INSERT INTO document_version_decision_records "
            "(id,org_id,workspace_id,document_id,document_version_id,"
            "lifecycle_revision,decision,actor_id,reason,created_at) VALUES "
            "(:id,:org,:workspace,:document,:version,1,'approved',:actor,NULL,:now)"
        )
        decision_connection.execute(
            insert_approved_decision_sql,
            {
                "id": uuid4(),
                "org": ids.org_id,
                "workspace": ids.workspace_id,
                "document": second_document,
                "version": decision_first_version,
                "actor": ids.user_id,
                "now": datetime(2026, 7, 19, 1),
            },
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                request_marker,
                decision_first_version,
                started,
            )
            assert started.wait(timeout=5)
            decision_transaction.commit()
            with pytest.raises(sa.exc.DBAPIError, match="governed history"):
                future.result(timeout=10)
    finally:
        if decision_transaction.is_active:
            decision_transaction.rollback()
        decision_connection.close()

    with engine.connect() as connection:
        marker_first = connection.execute(
            text("SELECT source_delete_requested_at FROM document_versions WHERE id=:id"),
            {"id": marker_first_version},
        ).scalar_one()
        decision_first = connection.execute(
            text("SELECT source_delete_requested_at FROM document_versions WHERE id=:id"),
            {"id": decision_first_version},
        ).scalar_one()
        assert marker_first is not None
        assert decision_first is None


def test_deletion_downgrade_fences_inflight_marker_before_preflight(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, DELETION_REVISION)
    failed_id = ids.document_ids["failed"]
    marker_connection = engine.connect()
    marker_transaction = marker_connection.begin()
    try:
        marker_connection.execute(
            text(
                "UPDATE document_versions SET source_delete_requested_at=:now, "
                "source_delete_requested_by=:actor WHERE id=:id"
            ),
            {
                "id": failed_id,
                "actor": ids.user_id,
                "now": datetime(2026, 7, 19, 1),
            },
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                command.downgrade,
                config,
                AUTHORITY_REVISION,
            )
            deadline = monotonic() + 5
            pending_lock = False
            while monotonic() < deadline and not pending_lock:
                with engine.connect() as observer:
                    pending_lock = bool(
                        observer.execute(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_locks locks "
                                "JOIN pg_class tables ON tables.oid=locks.relation "
                                "WHERE tables.relname='document_versions' "
                                "AND locks.mode='AccessExclusiveLock' "
                                "AND NOT locks.granted)"
                            )
                        ).scalar_one()
                    )
                if not pending_lock:
                    sleep(0.01)
            marker_transaction.commit()
            assert pending_lock, "downgrade never waited for the marker writer"
            with pytest.raises(RuntimeError, match="deletion history exists"):
                future.result(timeout=10)
    finally:
        if marker_transaction.is_active:
            marker_transaction.rollback()
        marker_connection.close()

    assert {
        "source_delete_requested_at",
        "source_delete_requested_by",
        "source_deleted_at",
    } <= {column["name"] for column in inspect(engine).get_columns("document_versions")}
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT source_delete_requested_at FROM document_versions WHERE id=:id"),
                {"id": failed_id},
            ).scalar_one()
            is not None
        )


def test_authority_upgrade_locks_all_compatibility_tables(
    authority_db: tuple[Config, Engine, object],
) -> None:
    _config, engine, _ids = authority_db
    migration = runpy.run_path(
        str(
            BACKEND_ROOT
            / "migrations"
            / "versions"
            / f"{AUTHORITY_REVISION}_document_authority_and_citations.py"
        )
    )
    lock_tables = cast(Callable[[sa.Connection], None], migration["_lock_legacy_tables"])
    with engine.begin() as connection:
        lock_tables(connection)
        rows = (
            connection.execute(
                text(
                    "SELECT tables.relname, locks.mode FROM pg_locks AS locks "
                    "JOIN pg_class AS tables ON tables.oid = locks.relation "
                    "WHERE locks.pid = pg_backend_pid() AND tables.relname = ANY(:tables)"
                ),
                {
                    "tables": [
                        "documents",
                        "ingest_jobs",
                        "chats",
                        "messages",
                        "citations",
                        "outbox_events",
                        "inbox_events",
                    ]
                },
            )
            .mappings()
            .all()
        )
    assert {row["relname"]: row["mode"] for row in rows} == {
        table: "ShareRowExclusiveLock"
        for table in (
            "documents",
            "ingest_jobs",
            "chats",
            "messages",
            "citations",
            "outbox_events",
            "inbox_events",
        )
    }


def test_authority_downgrade_fences_preflight_tables_in_global_lifecycle_order(
    authority_db: tuple[Config, Engine, object],
) -> None:
    config, engine, _ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    migration = runpy.run_path(
        str(
            BACKEND_ROOT
            / "migrations"
            / "versions"
            / f"{AUTHORITY_REVISION}_document_authority_and_citations.py"
        )
    )
    expected_order = (
        "workspaces",
        "documents",
        "document_versions",
        "document_version_projections",
        "grounding_policies",
        "grounding_calibration_runs",
        "document_authority_readiness",
        "ingest_jobs",
        "ingest_stage_attempts",
        "legacy_rebuild_scan_checkpoints",
        "document_blocks",
        "document_chunks",
        "document_chunk_blocks",
        "document_evidence_spans",
        "messages",
        "citations",
        "document_version_decision_records",
        "audit_events",
        "outbox_events",
        "inbox_events",
    )
    assert migration["_DOWNGRADE_PREFLIGHT_TABLES"] == expected_order
    lock_tables = cast(
        Callable[[sa.Connection], None],
        migration["_lock_downgrade_preflight_tables"],
    )

    with engine.begin() as connection:
        lock_tables(connection)
        rows = connection.execute(
            text(
                "SELECT tables.relname, locks.mode FROM pg_locks AS locks "
                "JOIN pg_class AS tables ON tables.oid=locks.relation "
                "WHERE locks.pid=pg_backend_pid() AND tables.relname=ANY(:tables)"
            ),
            {"tables": list(expected_order)},
        ).mappings()

        assert {row["relname"]: row["mode"] for row in rows} == {
            table: "AccessExclusiveLock" for table in expected_order
        }


def test_authority_upgrade_backfills_bounded_inbox_event_identity(
    authority_db: tuple[Config, Engine, object],
) -> None:
    config, engine, _ids = authority_db
    joined_event = uuid4()
    orphan_event = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO outbox_events "
                "(event_id, aggregate_type, aggregate_id, event_type, payload, "
                "dedupe_key, attempts, id, created_at) VALUES "
                "(:event, 'document_version', :aggregate, "
                "'document.version.rebuild_requested.v1', '{}'::json, :dedupe, 0, "
                ":id, now())"
            ),
            {
                "event": joined_event,
                "aggregate": uuid4(),
                "dedupe": str(uuid4()),
                "id": uuid4(),
            },
        )
        connection.execute(
            text(
                "INSERT INTO inbox_events (consumer, event_id, id, created_at) VALUES "
                "('document-start-v1', :joined, :joined_id, now()), "
                "('legacy-consumer', :orphan, :orphan_id, now())"
            ),
            {
                "joined": joined_event,
                "joined_id": uuid4(),
                "orphan": orphan_event,
                "orphan_id": uuid4(),
            },
        )

    command.upgrade(config, AUTHORITY_REVISION)
    with engine.connect() as connection:
        rows = dict(connection.execute(text("SELECT event_id, event_type FROM inbox_events")).all())
    assert rows == {
        joined_event: "document.version.rebuild_requested.v1",
        orphan_event: "legacy.unknown",
    }
    event_type_column = next(
        column
        for column in inspect(engine).get_columns("inbox_events")
        if column["name"] == "event_type"
    )
    assert event_type_column["nullable"] is False


def test_authority_upgrade_backfills_exact_legacy_versions_without_cutover(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT id, document_id, sequence, version_label, version_key, state, "
                    "provenance_state, source_filename, source_mime, source_size_bytes, "
                    "content_hash, source_storage_key, source_page_count, "
                    "parser_profile_version, ocr_profile_version, chunking_profile_version, "
                    "embedding_profile_version, index_profile_version "
                    "FROM document_versions ORDER BY source_filename"
                )
            )
            .mappings()
            .all()
        )
        assert len(rows) == 4
        expected_states = {
            "failed.pdf": ("failed", "none"),
            "indexed.pdf": ("approved", "legacy_pending"),
            "processing.pdf": ("processing", "none"),
            "queued.pdf": ("processing", "none"),
        }
        for row in rows:
            assert row["id"] == row["document_id"] == ids.document_ids[row["source_filename"][:-4]]
            assert (row["sequence"], row["version_label"], row["version_key"]) == (
                1,
                "Legacy 1",
                "legacy 1",
            )
            assert (row["state"], row["provenance_state"]) == expected_states[
                row["source_filename"]
            ]
            assert row["source_mime"] == "application/pdf"
            assert row["source_storage_key"] == f"legacy/{row['source_filename']}"
            assert row["source_size_bytes"] > 0
            assert len(row["content_hash"]) == 64
            assert (
                row["parser_profile_version"],
                row["ocr_profile_version"],
                row["chunking_profile_version"],
                row["embedding_profile_version"],
                row["index_profile_version"],
            ) == (
                "legacy/parser-v1",
                "legacy/ocr-unknown-v1",
                "legacy/chunking-v1",
                "legacy/embedding-v1",
                "legacy/index-v1",
            )
        indexed = next(row for row in rows if row["source_filename"] == "indexed.pdf")
        assert indexed["source_page_count"] == 7
        assert all(
            row["source_page_count"] is None
            for row in rows
            if row["source_filename"] != "indexed.pdf"
        )
        assert (
            connection.execute(
                text("SELECT document_authority_enabled FROM workspaces")
            ).scalar_one()
            is False
        )

        connection.execute(
            text(
                "UPDATE documents SET filename=NULL, mime=NULL, size_bytes=NULL, "
                "content_hash=NULL, storage_key=NULL, page_count=NULL "
                "WHERE id=:id"
            ),
            {"id": ids.document_ids["indexed"]},
        )
        rebuild_source = (
            connection.execute(
                text(
                    "SELECT source_filename, source_mime, source_size_bytes, content_hash, "
                    "source_storage_key, source_page_count FROM document_versions WHERE id=:id"
                ),
                {"id": ids.document_ids["indexed"]},
            )
            .mappings()
            .one()
        )
    assert dict(rebuild_source) == {
        "source_filename": "indexed.pdf",
        "source_mime": "application/pdf",
        "source_size_bytes": 100,
        "content_hash": f"{1:064x}",
        "source_storage_key": "legacy/indexed.pdf",
        "source_page_count": 7,
    }


def test_authority_upgrade_installs_scoped_version_and_signed_readiness_schema(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, _ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    inspector = inspect(engine)

    version_uniques = {
        constraint["name"]: tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("document_versions")
    }
    assert version_uniques["uq_document_versions_org_id"] == ("org_id", "id")
    assert version_uniques["uq_document_versions_org_workspace_document_id"] == (
        "org_id",
        "workspace_id",
        "document_id",
        "id",
    )

    decision_columns = {
        column["name"]: column
        for column in inspector.get_columns("document_version_decision_records")
    }
    assert set(decision_columns) >= {
        "org_id",
        "workspace_id",
        "document_id",
        "document_version_id",
        "lifecycle_revision",
        "decision",
        "actor_id",
        "reason",
        "created_at",
    }
    assert decision_columns["reason"]["nullable"] is True
    decision_uniques = {
        constraint["name"]: tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("document_version_decision_records")
    }
    assert decision_uniques["uq_document_version_decision_records_version_revision"] == (
        "org_id",
        "document_version_id",
        "lifecycle_revision",
    )
    decision_foreign_keys = {
        foreign_key["name"]: tuple(foreign_key["constrained_columns"])
        for foreign_key in inspector.get_foreign_keys("document_version_decision_records")
    }
    assert decision_foreign_keys["fk_document_version_decision_records_exact_version"] == (
        "org_id",
        "workspace_id",
        "document_id",
        "document_version_id",
    )
    assert decision_foreign_keys["fk_document_version_decision_records_org_actor"] == (
        "org_id",
        "actor_id",
    )

    readiness_columns = {
        column["name"]: column for column in inspector.get_columns("document_authority_readiness")
    }
    for required in (
        "generation_id",
        "org_id",
        "workspace_id",
        "request_digest",
        "calibration_hash",
        "expires_at",
    ):
        assert readiness_columns[required]["nullable"] is False
    assert "readiness_digest" in readiness_columns
    assert "signature" in readiness_columns

    readiness_foreign_keys = {
        foreign_key["name"]
        for foreign_key in inspector.get_foreign_keys("document_authority_readiness")
    }
    assert {
        "fk_document_authority_readiness_org_workspace",
        "fk_document_authority_readiness_policy_snapshot",
        "fk_document_authority_readiness_org_activator",
    } <= readiness_foreign_keys
    with engine.connect() as connection:
        immutable_artifact_triggers = set(
            connection.execute(
                text(
                    "SELECT trigger_name FROM information_schema.triggers "
                    "WHERE trigger_name LIKE 'trg_document_%_immutable'"
                )
            ).scalars()
        )
    assert {
        "trg_document_blocks_immutable",
        "trg_document_chunks_immutable",
        "trg_document_chunk_blocks_immutable",
        "trg_document_evidence_spans_immutable",
    } <= immutable_artifact_triggers


def test_authority_upgrade_aborts_before_mutation_for_orphan_citation(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    chat_id = uuid4()
    message_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO chats "
                "(id, org_id, workspace_id, user_id, title, updated_at, created_at) "
                "VALUES (:id, :org, :workspace, :user, 'Orphan', now(), now())"
            ),
            {
                "id": chat_id,
                "org": ids.org_id,
                "workspace": ids.workspace_id,
                "user": ids.user_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO messages "
                "(id, chat_id, parent_message_id, sibling_index, role, content, created_at) "
                "VALUES (:id, :chat, NULL, 0, 'assistant', 'orphan', now())"
            ),
            {"id": message_id, "chat": chat_id},
        )
        connection.execute(
            text(
                "INSERT INTO citations "
                "(id, message_id, document_id, chunk_ref, page, score, marker, created_at) "
                "VALUES (:id, :message, :document, 'missing:1', 1, 0.5, 1, now())"
            ),
            {"id": uuid4(), "message": message_id, "document": uuid4()},
        )

    with pytest.raises(RuntimeError, match="orphan or invalid legacy citation"):
        command.upgrade(config, AUTHORITY_REVISION)
    assert ScriptDirectory.from_config(config).get_current_head() == EMBEDDING_PROFILE_REVISION
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == RBAC_REVISION
        )
        assert not inspect(engine).has_table("document_versions")


@pytest.mark.parametrize("parent_role", ["user", "system"])
def test_authority_upgrade_aborts_before_mutation_for_invalid_legacy_parent(
    authority_db: tuple[Config, Engine, SimpleNamespace],
    parent_role: str,
) -> None:
    config, engine, ids = authority_db
    chat_id = uuid4()
    message_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO chats "
                "(id, org_id, workspace_id, user_id, title, updated_at, created_at) "
                "VALUES (:id, :org, :workspace, :user, 'Invalid parent', now(), now())"
            ),
            {
                "id": chat_id,
                "org": ids.org_id,
                "workspace": ids.workspace_id,
                "user": ids.user_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO messages "
                "(id, chat_id, parent_message_id, sibling_index, role, content, created_at) "
                "VALUES (:id, :chat, NULL, 0, :role, 'invalid parent', now())"
            ),
            {"id": message_id, "chat": chat_id, "role": parent_role},
        )
        connection.execute(
            text(
                "INSERT INTO citations "
                "(id, message_id, document_id, chunk_ref, page, score, marker, created_at) "
                "VALUES (:id, :message, :document, 'legacy:1', 1, 0.5, 1, now())"
            ),
            {
                "id": uuid4(),
                "message": message_id,
                "document": ids.document_ids["indexed"],
            },
        )

    with pytest.raises(RuntimeError, match="invalid legacy citation"):
        command.upgrade(config, AUTHORITY_REVISION)
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == RBAC_REVISION
        )
    assert not inspect(engine).has_table("document_versions")


@pytest.mark.parametrize(
    "unsafe_sql",
    [
        "UPDATE workspaces SET document_authority_enabled=true",
        "INSERT INTO outbox_events "
        "(event_id, aggregate_type, aggregate_id, event_type, payload, dedupe_key, "
        "attempts, id, created_at) "
        "SELECT gen_random_uuid(), 'document_version', id, 'document.version.ready', '{}'::json, "
        "gen_random_uuid()::text, 0, gen_random_uuid(), now() FROM document_versions LIMIT 1",
    ],
)
def test_authority_downgrade_refuses_non_lossless_state(
    authority_db: tuple[Config, Engine, object],
    unsafe_sql: str,
) -> None:
    config, engine, _ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with engine.begin() as connection:
        connection.execute(text(unsafe_sql))
    with pytest.raises(RuntimeError, match="downgrade preflight"):
        command.downgrade(config, RBAC_REVISION)


def test_authority_downgrade_rejects_a_nonlegacy_sole_version_even_with_none_provenance(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE document_versions DISABLE TRIGGER trg_document_versions_immutable")
        )
        connection.execute(
            text(
                "UPDATE document_versions SET sequence=2, version_label='Draft 2', "
                "version_key='draft 2', state='draft', provenance_state='none', "
                "parser_profile_version='parser/v2', ocr_profile_version='none/v1', "
                "chunking_profile_version='chunking/v2', "
                "embedding_profile_version='embedding/v2', "
                "index_profile_version='index/v2' WHERE id=:id"
            ),
            {"id": ids.document_ids["indexed"]},
        )
        connection.execute(
            text("ALTER TABLE document_versions ENABLE TRIGGER trg_document_versions_immutable")
        )

    with pytest.raises(RuntimeError, match="downgrade preflight.*exact legacy"):
        command.downgrade(config, RBAC_REVISION)


def test_authority_downgrade_round_trips_exact_legacy_source_state(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, AUTHORITY_REVISION)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE documents SET filename=NULL, mime=NULL, size_bytes=NULL, "
                "content_hash=NULL, storage_key=NULL, page_count=NULL WHERE id=:id"
            ),
            {"id": ids.document_ids["indexed"]},
        )

    command.downgrade(config, RBAC_REVISION)
    inspector = inspect(engine)
    assert "document_versions" not in inspector.get_table_names()
    assert "document_authority_enabled" not in {
        column["name"] for column in inspector.get_columns("workspaces")
    }
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT filename, mime, size_bytes, content_hash, storage_key, page_count "
                    "FROM documents WHERE id=:id"
                ),
                {"id": ids.document_ids["indexed"]},
            )
            .mappings()
            .one()
        )
    assert dict(row) == {
        "filename": "indexed.pdf",
        "mime": "application/pdf",
        "size_bytes": 100,
        "content_hash": f"{1:064x}",
        "storage_key": "legacy/indexed.pdf",
        "page_count": 7,
    }


def test_outbox_hardening_preflight_stops_before_ddl_on_unsafe_legacy_rows(
    pre_outbox_database: tuple[Config, Engine],
) -> None:
    config, engine = pre_outbox_database
    with engine.begin() as connection:
        row_id = insert_legacy_outbox(
            connection,
            attempts=-1,
            event_type="e" * 201,
            aggregate_type="a" * 121,
            dedupe_key="d" * 256,
            lease_owner="l" * 129,
            last_error="SENTINEL raw database exception with credentials",
            payload={"document_text": "x" * 20_000},
        )

    with pytest.raises(sa.exc.DBAPIError, match="OPENRAG_OUTBOX_PREFLIGHT_FAILED"):
        command.upgrade(config, OUTBOX_REVISION)

    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("outbox_events")}
    assert "dispatch_after" not in columns
    assert "last_error" in columns
    assert "last_error_code" not in columns
    with engine.begin() as connection:
        attempts = connection.scalar(
            text("SELECT attempts FROM outbox_events WHERE id=:id"), {"id": row_id}
        )
    assert attempts == -1


def test_outbox_hardening_normalizes_errors_and_leases_then_downgrades_safely(
    pre_outbox_database: tuple[Config, Engine],
) -> None:
    config, engine = pre_outbox_database
    event_type = "document.version.lifecycle.v1"
    aggregate_type = "document_version"
    payload = {"previous_state": "review", "new_state": "approved"}
    with engine.begin() as connection:
        row_id = insert_legacy_outbox(
            connection,
            event_type=event_type,
            aggregate_type=aggregate_type,
            attempts=3,
            lease_owner="legacy-worker",
            lease_expires_at=datetime(2027, 7, 19),
            last_error="SENTINEL psycopg password=do-not-leak",
            payload=payload,
        )

    command.upgrade(config, OUTBOX_REVISION)

    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("outbox_events")}
    assert {
        "dispatch_after",
        "dead_lettered_at",
        "last_error_code",
        "lease_token",
        "envelope_digest",
        "published_stream",
        "published_message_id",
    } <= columns
    assert "last_error" not in columns
    index = next(
        item
        for item in inspector.get_indexes("outbox_events")
        if item["name"] == "ix_outbox_events_claimable"
    )
    assert "published_at IS NULL" in str(index["dialect_options"]["postgresql_where"])
    assert "dead_lettered_at IS NULL" in str(index["dialect_options"]["postgresql_where"])
    with engine.begin() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT event_type,aggregate_type,payload,attempts,lease_owner,"
                    "lease_expires_at,lease_token,last_error_code,envelope_digest "
                    "FROM outbox_events WHERE id=:id"
                ),
                {"id": row_id},
            )
            .mappings()
            .one()
        )
    assert row["event_type"] == event_type
    assert row["aggregate_type"] == aggregate_type
    assert row["payload"] == payload
    assert row["attempts"] == 3
    assert row["lease_owner"] is None
    assert row["lease_expires_at"] is None
    assert row["lease_token"] is None
    assert row["last_error_code"] == "legacy_dispatch_failure"
    assert row["envelope_digest"] is None
    assert "SENTINEL" not in repr(row)

    command.downgrade(config, DELETION_REVISION)
    columns = {column["name"] for column in inspect(engine).get_columns("outbox_events")}
    assert "last_error" in columns
    assert "last_error_code" not in columns
    assert "dispatch_after" not in columns
    with engine.begin() as connection:
        downgraded_error = connection.scalar(
            text("SELECT last_error FROM outbox_events WHERE id=:id"), {"id": row_id}
        )
    assert downgraded_error == "legacy_dispatch_failure"


def test_durable_stage_upgrade_fences_existing_queued_rows_and_downgrades(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, OUTBOX_REVISION)
    attempt_id = uuid4()
    version_id = ids.document_ids["indexed"]
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ingest_stage_attempts "
                "(id,org_id,workspace_id,document_version_id,pipeline_kind,stage,state,"
                "checkpoint,attempts,created_at) VALUES "
                "(:id,:org,:workspace,:version,'rebuild','parse','queued',"
                ":checkpoint,0,:created_at)"
            ),
            {
                "id": attempt_id,
                "org": ids.org_id,
                "workspace": ids.workspace_id,
                "version": version_id,
                "checkpoint": f"parse:rebuild:1:{uuid4().hex}",
                "created_at": datetime(2026, 7, 20),
            },
        )

    command.upgrade(config, STAGE_REVISION)

    inspector = inspect(engine)
    columns = {
        column["name"] for column in inspector.get_columns("ingest_stage_attempts")
    }
    assert {"lease_token", "available_at", "output_digest"} <= columns
    assert "ix_ingest_stage_attempts_claimable" in {
        index["name"] for index in inspector.get_indexes("ingest_stage_attempts")
    }
    with engine.begin() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT state,attempts,available_at,lease_owner,lease_token,"
                    "lease_expires_at,output_digest FROM ingest_stage_attempts WHERE id=:id"
                ),
                {"id": attempt_id},
            )
            .mappings()
            .one()
        )
    assert row["state"] == "queued"
    assert row["attempts"] == 0
    assert row["available_at"] is not None
    assert row["lease_owner"] is None
    assert row["lease_token"] is None
    assert row["lease_expires_at"] is None
    assert row["output_digest"] is None

    with engine.begin() as connection, pytest.raises(sa.exc.DBAPIError):
        connection.execute(
            text(
                "UPDATE ingest_stage_attempts SET state='running' WHERE id=:id"
            ),
            {"id": attempt_id},
        )

    command.downgrade(config, OUTBOX_REVISION)
    downgraded = {
        column["name"] for column in inspect(engine).get_columns("ingest_stage_attempts")
    }
    assert "lease_token" not in downgraded
    assert "available_at" not in downgraded
    assert "output_digest" not in downgraded


def test_legacy_rebuild_revision_opens_then_seals_evidence_and_blocks_downgrade(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, REBUILD_REVISION)
    version_id = ids.document_ids["indexed"]

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_versions SET provenance_state='building' "
                "WHERE id=:id"
            ),
            {"id": version_id},
        )
        insert_document_block(connection, ids, version_id)
        connection.execute(
            text(
                "UPDATE document_versions SET provenance_state='ready' "
                "WHERE id=:id"
            ),
            {"id": version_id},
        )

    with engine.begin() as connection, pytest.raises(
        sa.exc.DBAPIError,
        match="evidence artifact mutation requires processing/building owner",
    ):
        insert_document_block(connection, ids, version_id)

    with pytest.raises(RuntimeError, match="governed rebuild state exists"):
        command.downgrade(config, STAGE_REVISION)
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == REBUILD_REVISION
        )


def test_clean_legacy_rebuild_revision_downgrades_to_stage_contract(
    authority_db: tuple[Config, Engine, object],
) -> None:
    config, engine, _ids = authority_db
    command.upgrade(config, REBUILD_REVISION)

    command.downgrade(config, STAGE_REVISION)

    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == STAGE_REVISION
        )


def test_durable_legacy_ingestion_revision_allows_claim_and_review_then_fences_downgrade(
    authority_db: tuple[Config, Engine, SimpleNamespace],
) -> None:
    config, engine, ids = authority_db
    command.upgrade(config, RUNTIME_REVISION)
    version_id = ids.document_ids["processing"]

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_versions SET provenance_state='building' "
                "WHERE id=:id"
            ),
            {"id": version_id},
        )
        assert connection.scalar(
            text("SELECT source_page_count FROM document_versions WHERE id=:id"),
            {"id": version_id},
        ) is None
        connection.execute(
            text(
                "UPDATE document_versions SET source_page_count=1, "
                "state='review', provenance_state='ready' WHERE id=:id"
            ),
            {"id": version_id},
        )

    with pytest.raises(RuntimeError, match="governed state exists"):
        command.downgrade(config, REBUILD_REVISION)


def test_clean_durable_legacy_ingestion_revision_downgrades(
    authority_db: tuple[Config, Engine, object],
) -> None:
    config, engine, _ids = authority_db
    command.upgrade(config, RUNTIME_REVISION)

    command.downgrade(config, REBUILD_REVISION)

    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == REBUILD_REVISION
        )


def test_outbox_hardening_constraints_reject_untruthful_terminal_state(
    pre_outbox_database: tuple[Config, Engine],
) -> None:
    config, engine = pre_outbox_database
    with engine.begin() as connection:
        row_id = insert_legacy_outbox(connection)
    command.upgrade(config, OUTBOX_REVISION)

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE outbox_events SET dead_lettered_at=now(), "
                "last_error_code='contract_invalid' WHERE id=:id"
            ),
            {"id": row_id},
        )
        with pytest.raises(sa.exc.DBAPIError):
            connection.execute(
                text("UPDATE outbox_events SET published_at=now() WHERE id=:id"),
                {"id": row_id},
            )
