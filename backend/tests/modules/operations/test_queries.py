from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from openrag.modules.operations.queries import build_overview_query
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
