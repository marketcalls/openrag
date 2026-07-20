from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from openrag.modules.operations.models import ErrorIssue
from openrag.modules.operations.queries import (
    build_answer_quality_overview_query,
    build_error_issue_detail_query,
    build_error_list_query,
    build_error_occurrence_detail_query,
    build_overview_query,
    build_run_detail_query,
    build_run_list_query,
    build_series_query,
    decode_operations_cursor,
    encode_operations_cursor,
    scoped_error_issue_out,
)
from openrag.modules.operations.schemas import AnswerQualityFilter, RagOperationsFilter


def _window(**changes: object) -> RagOperationsFilter:
    values: dict[str, object] = {
        "from_at": datetime(2026, 7, 19, tzinfo=UTC),
        "to_at": datetime(2026, 7, 20, tzinfo=UTC),
    }
    values.update(changes)
    return RagOperationsFilter(**values)  # type: ignore[arg-type]


def test_operations_filter_rejects_unbounded_or_reversed_windows() -> None:
    with pytest.raises(ValidationError):
        _window(to_at=datetime(2027, 1, 1, tzinfo=UTC))
    with pytest.raises(ValidationError):
        _window(
            from_at=datetime(2026, 7, 20, tzinfo=UTC),
            to_at=datetime(2026, 7, 19, tzinfo=UTC),
        )


def test_overview_query_is_database_aggregated_and_scope_filtered() -> None:
    filters = _window(
        org_id=uuid4(),
        workspace_id=uuid4(),
        route="rag",
        outcome="grounded",
        model_id=uuid4(),
        environment="prod",
    )

    sql = str(
        build_overview_query(filters).compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": False},
        )
    )

    assert "percentile_cont" in sql
    assert "FILTER (WHERE rag_run_facts.outcome" in sql
    assert "rag_run_facts.org_id" in sql
    assert "rag_run_facts.workspace_id" in sql
    assert "rag_run_facts.route" in sql
    assert "rag_run_facts.model_id" in sql
    assert "rag_run_facts.environment" in sql


def test_answer_quality_query_is_content_free_aggregated_and_scope_filtered() -> None:
    filters = AnswerQualityFilter(
        from_at=datetime(2026, 7, 19, tzinfo=UTC),
        to_at=datetime(2026, 7, 20, tzinfo=UTC),
        org_id=uuid4(),
        workspace_id=uuid4(),
        model_id=uuid4(),
    )

    sql = str(
        build_answer_quality_overview_query(filters).compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": False},
        )
    )

    assert "answer_quality_audits" in sql
    assert "messages" in sql
    assert "grounding_score" in sql
    assert "completeness_score" in sql
    assert "FILTER (WHERE answer_quality_audits.status" in sql
    assert "answer_quality_audits.org_id" in sql
    assert "answer_quality_audits.workspace_id" in sql
    assert "messages.model_id" in sql
    assert "message.content" not in sql
    assert "messages.content" not in sql


def test_operations_filter_accepts_at_most_ninety_days() -> None:
    start = datetime(2026, 4, 21, tzinfo=UTC)

    filters = _window(from_at=start, to_at=start + timedelta(days=90))

    assert filters.to_at - filters.from_at == timedelta(days=90)


def test_series_query_uses_database_buckets_and_percentiles() -> None:
    sql = str(
        build_series_query(_window(), interval="hour").compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": False},
        )
    )

    assert "date_trunc" in sql
    assert "percentile_cont" in sql
    assert "GROUP BY" in sql
    assert "ORDER BY" in sql


def test_run_list_query_is_keyset_paginated_and_bounded() -> None:
    cursor_time = datetime(2026, 7, 19, 12, tzinfo=UTC)
    cursor_id = uuid4()
    sql = str(
        build_run_list_query(
            _window(),
            cursor=(cursor_time, cursor_id),
            limit=100,
        ).compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "accepted_at <" in sql
    assert "accepted_at =" in sql
    assert "ORDER BY rag_run_facts.accepted_at DESC" in sql
    assert "LIMIT 101" in sql


def test_error_list_query_aggregates_only_occurrences_in_active_scope() -> None:
    sql = str(
        build_error_list_query(
            _window(org_id=uuid4(), workspace_id=uuid4()),
            cursor=None,
            limit=25,
        ).compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": False},
        )
    )

    assert "JOIN" in sql
    assert "count(error_occurrences.id)" in sql
    assert "min(error_occurrences.occurred_at)" in sql
    assert "max(error_occurrences.occurred_at)" in sql
    assert "error_occurrences.org_id" in sql
    assert "error_occurrences.workspace_id" in sql
    assert "GROUP BY error_occurrences.issue_id" in sql
    assert "LIMIT" in sql


def test_run_detail_query_cannot_escape_active_tenant_scope() -> None:
    filters = _window(org_id=uuid4(), workspace_id=uuid4())
    sql = str(
        build_run_detail_query(uuid4(), filters).compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": False},
        )
    )

    assert "rag_run_facts.run_id" in sql
    assert "rag_run_facts.org_id" in sql
    assert "rag_run_facts.workspace_id" in sql
    assert "rag_run_facts.accepted_at" in sql


def test_error_detail_queries_cannot_mix_occurrences_across_tenant_scope() -> None:
    filters = _window(org_id=uuid4(), workspace_id=uuid4())
    issue_id = uuid4()
    issue_sql = str(
        build_error_issue_detail_query(issue_id, filters).compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": False},
        )
    )
    occurrence_sql = str(
        build_error_occurrence_detail_query(issue_id, filters).compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "JOIN" in issue_sql
    assert "count(error_occurrences.id)" in issue_sql
    assert "error_occurrences.org_id" in issue_sql
    assert "error_occurrences.workspace_id" in issue_sql
    assert "error_occurrences.org_id" in occurrence_sql
    assert "error_occurrences.workspace_id" in occurrence_sql
    assert "error_occurrences.occurred_at" in occurrence_sql
    assert "LIMIT 100" in occurrence_sql


def test_scoped_error_output_replaces_global_count_and_timestamps() -> None:
    issue = ErrorIssue(
        id=uuid4(),
        fingerprint="a" * 64,
        category="retrieval",
        code="retrieval.timeout",
        service="api",
        environment="prod",
        exception_type="TimeoutError",
        status="open",
        alert_state="none",
        occurrence_count=500,
        first_seen_at=datetime(2026, 1, 1),
        last_seen_at=datetime(2026, 7, 20),
    )
    scoped_first = datetime(2026, 7, 19, 10)
    scoped_last = datetime(2026, 7, 19, 12)

    output = scoped_error_issue_out(
        issue,
        occurrence_count=2,
        first_seen_at=scoped_first,
        last_seen_at=scoped_last,
    )

    assert output.occurrence_count == 2
    assert output.first_seen_at == scoped_first
    assert output.last_seen_at == scoped_last


def test_operations_cursor_round_trips_and_rejects_malformed_input() -> None:
    accepted_at = datetime(2026, 7, 19, 12, 30, 45, 123456, tzinfo=UTC)
    item_id = uuid4()

    encoded = encode_operations_cursor(accepted_at, item_id)

    assert decode_operations_cursor(encoded) == (accepted_at, item_id)
    with pytest.raises(ValueError, match="operations_cursor_invalid"):
        decode_operations_cursor("../../not-a-cursor")
