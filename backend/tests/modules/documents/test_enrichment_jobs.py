from datetime import datetime
from uuid import UUID

from sqlalchemy.dialects import postgresql

from openrag.modules.documents.enrichment_jobs import (
    build_enrichment_claim_query,
    build_enrichment_workspace_page_query,
)
from openrag.modules.documents.models import DocumentEnrichmentJob
from openrag.modules.tenancy.models import Workspace


def test_enrichment_job_schema_is_lease_fenced_and_generation_bound() -> None:
    table = DocumentEnrichmentJob.__table__
    expected = {
        "org_id",
        "workspace_id",
        "document_version_id",
        "embedding_deployment_id",
        "model_id",
        "model_probe_revision",
        "prompt_contract_version",
        "evidence_start_ordinal",
        "evidence_end_ordinal",
        "source",
        "status",
        "attempts",
        "lease_owner",
        "lease_token",
        "lease_expires_at",
        "total_evidence",
        "generated_evidence",
        "invalid_evidence",
        "prompt_tokens",
        "completion_tokens",
        "error_code",
        "requested_by",
        "started_at",
        "finished_at",
    }
    assert expected.issubset(table.c.keys())
    constraints = {constraint.name for constraint in table.constraints}
    assert "uq_document_enrichment_jobs_generation" in constraints
    assert "ck_document_enrichment_jobs_status" in constraints
    assert "ck_document_enrichment_jobs_lease" in constraints
    assert "ck_document_enrichment_jobs_batch" in constraints
    assert "ck_document_enrichment_jobs_results" in constraints
    assert "ix_document_enrichment_jobs_claim" in {
        index.name for index in table.indexes
    }


def test_workspace_enrichment_is_explicitly_off_by_default() -> None:
    column = Workspace.__table__.c.enrichment_enabled
    assert column.nullable is False
    assert str(column.server_default.arg) == "false"  # type: ignore[union-attr]


def test_enrichment_claim_query_is_bounded_and_skip_locked() -> None:
    statement = build_enrichment_claim_query(datetime(2026, 7, 20, 12, 0))
    rendered = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "document_enrichment_jobs.attempts < 8" in rendered
    assert "document_enrichment_jobs.status = 'queued'" in rendered
    assert "document_enrichment_jobs.lease_expires_at <= '2026-07-20 12:00:00'" in rendered
    assert "LIMIT 1" in rendered
    assert "SKIP LOCKED" in rendered


def test_backfill_workspace_page_excludes_fully_scheduled_workspaces() -> None:
    statement = build_enrichment_workspace_page_query(
        model_id=UUID("10000000-0000-0000-0000-000000000001"),
        model_probe_revision=3,
        embedding_deployment_id=UUID("20000000-0000-0000-0000-000000000002"),
    )
    rendered = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "workspaces.enrichment_enabled IS true" in rendered
    assert "NOT (EXISTS" in rendered
    assert "document_versions.state = 'approved'" in rendered
    assert "document_enrichment_jobs.model_probe_revision = 3" in rendered
    assert "LIMIT 100" in rendered
