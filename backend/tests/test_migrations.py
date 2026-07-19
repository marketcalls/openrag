import runpy
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
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


def test_authority_revision_is_the_single_head(authority_db: tuple[Config, Engine, object]) -> None:
    config, _engine, _ids = authority_db
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == [AUTHORITY_REVISION]
    assert script.get_revision(AUTHORITY_REVISION).down_revision == RBAC_REVISION


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
    assert ScriptDirectory.from_config(config).get_current_head() == AUTHORITY_REVISION
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
