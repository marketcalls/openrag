"""Bounded database-side read models for platform RAG operations."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from openrag.modules.operations.models import RagRunFact
from openrag.modules.operations.schemas import (
    RagOperationsFilter,
    RagOperationsOverview,
)


def _database_time(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def build_overview_query(filters: RagOperationsFilter) -> Select[tuple[object, ...]]:
    conditions = [
        RagRunFact.accepted_at >= _database_time(filters.from_at),
        RagRunFact.accepted_at < _database_time(filters.to_at),
    ]
    for column, value in (
        (RagRunFact.org_id, filters.org_id),
        (RagRunFact.workspace_id, filters.workspace_id),
        (RagRunFact.route, filters.route),
        (RagRunFact.outcome, filters.outcome),
        (RagRunFact.model_id, filters.model_id),
        (RagRunFact.environment, filters.environment),
        (RagRunFact.release, filters.release),
    ):
        if value is not None:
            conditions.append(column == value)
    return select(
        func.count().label("query_count"),
        func.count().filter(RagRunFact.outcome == "grounded").label("grounded_count"),
        func.count().filter(RagRunFact.outcome == "no_answer").label("no_answer_count"),
        func.count().filter(RagRunFact.outcome == "failed").label("failed_count"),
        func.count().filter(RagRunFact.outcome == "cancelled").label("cancelled_count"),
        func.percentile_cont(0.5).within_group(RagRunFact.latency_ms).label("p50_latency_ms"),
        func.percentile_cont(0.95).within_group(RagRunFact.latency_ms).label("p95_latency_ms"),
        func.percentile_cont(0.99).within_group(RagRunFact.latency_ms).label("p99_latency_ms"),
        func.avg(RagRunFact.ttft_ms).label("average_ttft_ms"),
        func.coalesce(func.sum(RagRunFact.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(RagRunFact.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(RagRunFact.estimated_cost_microusd), 0).label(
            "estimated_cost_microusd"
        ),
    ).where(*conditions)


async def get_overview(
    session: AsyncSession,
    filters: RagOperationsFilter,
) -> RagOperationsOverview:
    row = (await session.execute(build_overview_query(filters))).one()
    query_count = int(row.query_count or 0)
    grounded_count = int(row.grounded_count or 0)
    no_answer_count = int(row.no_answer_count or 0)
    return RagOperationsOverview(
        query_count=query_count,
        grounded_count=grounded_count,
        no_answer_count=no_answer_count,
        failed_count=int(row.failed_count or 0),
        cancelled_count=int(row.cancelled_count or 0),
        grounded_rate=(grounded_count / query_count if query_count else 0.0),
        no_answer_rate=(no_answer_count / query_count if query_count else 0.0),
        p50_latency_ms=(float(row.p50_latency_ms) if row.p50_latency_ms is not None else None),
        p95_latency_ms=(float(row.p95_latency_ms) if row.p95_latency_ms is not None else None),
        p99_latency_ms=(float(row.p99_latency_ms) if row.p99_latency_ms is not None else None),
        average_ttft_ms=(float(row.average_ttft_ms) if row.average_ttft_ms is not None else None),
        prompt_tokens=int(row.prompt_tokens or 0),
        completion_tokens=int(row.completion_tokens or 0),
        estimated_cost_microusd=int(row.estimated_cost_microusd or 0),
    )
