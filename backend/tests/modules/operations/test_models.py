import importlib
from io import StringIO

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from openrag.modules.operations.models import ErrorIssue, ErrorOccurrence, RagRunFact


def test_rag_fact_contains_metrics_but_no_raw_content_fields() -> None:
    fields = set(RagRunFact.__table__.columns.keys())

    assert {
        "run_id",
        "route",
        "outcome",
        "latency_ms",
        "ttft_ms",
        "retrieval_ms",
        "provider_ms",
        "prompt_tokens",
        "completion_tokens",
        "citation_count",
    } <= fields
    assert not fields & {
        "prompt",
        "response",
        "query",
        "document_text",
        "filename",
        "memory",
        "provider_payload",
        "exception_message",
    }


def test_error_tables_contain_safe_grouping_metadata_only() -> None:
    issue_fields = set(ErrorIssue.__table__.columns.keys())
    occurrence_fields = set(ErrorOccurrence.__table__.columns.keys())

    assert {
        "fingerprint",
        "category",
        "code",
        "service",
        "environment",
        "exception_type",
        "occurrence_count",
    } <= issue_fields
    assert {
        "issue_id",
        "trace_id",
        "run_id",
        "route_template",
        "http_status",
        "release",
    } <= occurrence_fields
    forbidden = {"message", "detail", "stacktrace", "headers", "body", "payload"}
    assert not issue_fields & forbidden
    assert not occurrence_fields & forbidden


def test_operations_tables_compile_for_postgresql() -> None:
    dialect = postgresql.dialect()

    for table in (RagRunFact.__table__, ErrorIssue.__table__, ErrorOccurrence.__table__):
        sql = str(CreateTable(table).compile(dialect=dialect))
        assert f"CREATE TABLE {table.name}" in sql


def test_operations_constraints_are_named_and_tenant_bound() -> None:
    fact_constraints = {constraint.name for constraint in RagRunFact.__table__.constraints}
    occurrence_constraints = {
        constraint.name for constraint in ErrorOccurrence.__table__.constraints
    }

    assert "uq_rag_run_facts_org_run" in fact_constraints
    assert "fk_rag_run_facts_org_workspace_run" in fact_constraints
    assert "ck_rag_run_facts_metrics_nonnegative" in fact_constraints
    assert "fk_error_occurrences_org_workspace_run" in occurrence_constraints
    assert "ck_error_occurrences_run_scope" in occurrence_constraints


def test_operations_have_composite_scope_time_indexes_for_bounded_reads() -> None:
    fact_indexes = {index.name for index in RagRunFact.__table__.indexes}
    occurrence_indexes = {index.name for index in ErrorOccurrence.__table__.indexes}

    assert "ix_rag_run_facts_org_workspace_time" in fact_indexes
    assert "ix_error_occurrences_org_workspace_time" in occurrence_indexes


def test_operations_migration_generates_postgresql_upgrade_and_downgrade_sql(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    migration = importlib.import_module("migrations.versions.c8e0a3b5d7f9_rag_operations_facts")
    assert migration.down_revision == "b7d9f2a4c6e8"

    upgrade_buffer = StringIO()
    upgrade_context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": upgrade_buffer},
    )
    monkeypatch.setattr(migration, "op", Operations(upgrade_context))
    migration.upgrade()
    upgrade_sql = upgrade_buffer.getvalue()
    assert "CREATE TABLE rag_run_facts" in upgrade_sql
    assert "CREATE TABLE error_issues" in upgrade_sql
    assert "CREATE TABLE error_occurrences" in upgrade_sql

    downgrade_buffer = StringIO()
    downgrade_context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": downgrade_buffer},
    )
    monkeypatch.setattr(migration, "op", Operations(downgrade_context))
    migration.downgrade()
    downgrade_sql = downgrade_buffer.getvalue()
    assert "DROP TABLE error_occurrences" in downgrade_sql
    assert "DROP TABLE error_issues" in downgrade_sql
    assert "DROP TABLE rag_run_facts" in downgrade_sql


def test_scope_index_migration_is_online_and_reversible(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    migration = importlib.import_module(
        "migrations.versions.e2a4c6d8f0b1_scope_operations_indexes"
    )
    assert migration.down_revision == "d9f1b4c6e8a0"

    upgrade_buffer = StringIO()
    upgrade_context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": upgrade_buffer},
    )
    monkeypatch.setattr(migration, "op", Operations(upgrade_context))
    migration.upgrade()
    upgrade_sql = upgrade_buffer.getvalue()
    assert "CREATE INDEX CONCURRENTLY ix_rag_run_facts_org_workspace_time" in upgrade_sql
    assert "CREATE INDEX CONCURRENTLY ix_error_occurrences_org_workspace_time" in upgrade_sql

    downgrade_buffer = StringIO()
    downgrade_context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": downgrade_buffer},
    )
    monkeypatch.setattr(migration, "op", Operations(downgrade_context))
    migration.downgrade()
    downgrade_sql = downgrade_buffer.getvalue()
    assert "DROP INDEX CONCURRENTLY ix_error_occurrences_org_workspace_time" in downgrade_sql
    assert "DROP INDEX CONCURRENTLY ix_rag_run_facts_org_workspace_time" in downgrade_sql
