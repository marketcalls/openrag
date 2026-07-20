from datetime import datetime

from sqlalchemy.dialects import postgresql

from openrag.modules.operations.queue_metrics import build_queue_age_query


def test_queue_age_query_is_one_bounded_union_over_fixed_durable_queues() -> None:
    sql = str(
        build_queue_age_query(datetime(2026, 7, 20, 12)).compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )

    assert sql.count("UNION ALL") == 5
    assert "agent_runs" in sql
    assert "ingest_stage_attempts" in sql
    assert "conversation_summary_jobs" in sql
    assert "evaluation_runs" in sql
    assert "outbox_events" in sql
    assert "document_version_projections" in sql
    assert sql.count("min(") == 6
    for queue in ("runs", "ingestion", "summaries", "evaluations", "outbox", "embeddings"):
        assert f"'{queue}'" in sql
