from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from openrag.modules.operations.queries import (
    build_error_list_query,
    build_overview_query,
    build_run_list_query,
    build_series_query,
    decode_operations_cursor,
    encode_operations_cursor,
)
from openrag.modules.operations.schemas import RagOperationsFilter


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


def test_error_list_query_uses_occurrence_scope_without_loading_occurrences() -> None:
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

    assert "EXISTS" in sql
    assert "error_occurrences.org_id" in sql
    assert "error_occurrences.workspace_id" in sql
    assert "LIMIT" in sql


def test_operations_cursor_round_trips_and_rejects_malformed_input() -> None:
    accepted_at = datetime(2026, 7, 19, 12, 30, 45, 123456, tzinfo=UTC)
    item_id = uuid4()

    encoded = encode_operations_cursor(accepted_at, item_id)

    assert decode_operations_cursor(encoded) == (accepted_at, item_id)
    with pytest.raises(ValueError, match="operations_cursor_invalid"):
        decode_operations_cursor("../../not-a-cursor")
