"""Bounded database-side read models for platform RAG operations."""

import base64
import binascii
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from openrag.core.errors import NotFoundError
from openrag.modules.operations.models import ErrorIssue, ErrorOccurrence, RagRunFact
from openrag.modules.operations.schemas import (
    ErrorIssueOut,
    ErrorOccurrenceOut,
    RagOperationsErrorDetail,
    RagOperationsErrorPage,
    RagOperationsFilter,
    RagOperationsOverview,
    RagOperationsRunOut,
    RagOperationsRunPage,
    RagOperationsSeriesPoint,
    RagSeriesInterval,
)


def _database_time(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def _fact_conditions(filters: RagOperationsFilter) -> list[ColumnElement[bool]]:
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
    return conditions


def encode_operations_cursor(value_at: datetime, item_id: UUID) -> str:
    aware = value_at if value_at.tzinfo is not None else value_at.replace(tzinfo=UTC)
    payload = json.dumps(
        [aware.astimezone(UTC).isoformat(), str(item_id)],
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_operations_cursor(cursor: str) -> tuple[datetime, UUID]:
    if not 1 <= len(cursor) <= 512:
        raise ValueError("operations_cursor_invalid")
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        payload = json.loads(raw)
        if not isinstance(payload, list) or len(payload) != 2:
            raise ValueError
        value_at = datetime.fromisoformat(payload[0])
        item_id = UUID(payload[1])
        if value_at.tzinfo is None:
            raise ValueError
        return value_at.astimezone(UTC), item_id
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("operations_cursor_invalid") from exc


def build_overview_query(filters: RagOperationsFilter) -> Select[tuple[object, ...]]:
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
    ).where(*_fact_conditions(filters))


def build_series_query(
    filters: RagOperationsFilter,
    *,
    interval: str,
) -> Select[tuple[object, ...]]:
    if interval not in {"hour", "day"}:
        raise ValueError("operations_interval_invalid")
    bucket = func.date_trunc(interval, RagRunFact.accepted_at).label("bucket")
    return (
        select(
            bucket,
            func.count().label("query_count"),
            func.count().filter(RagRunFact.outcome == "grounded").label("grounded_count"),
            func.count().filter(RagRunFact.outcome == "no_answer").label("no_answer_count"),
            func.count().filter(RagRunFact.outcome == "failed").label("failed_count"),
            func.percentile_cont(0.95).within_group(RagRunFact.latency_ms).label("p95_latency_ms"),
        )
        .where(*_fact_conditions(filters))
        .group_by(bucket)
        .order_by(bucket)
    )


def build_run_list_query(
    filters: RagOperationsFilter,
    *,
    cursor: tuple[datetime, UUID] | None,
    limit: int,
) -> Select[tuple[RagRunFact]]:
    if not 1 <= limit <= 100:
        raise ValueError("operations_limit_invalid")
    conditions = _fact_conditions(filters)
    if cursor is not None:
        cursor_at, cursor_id = cursor
        database_cursor = _database_time(cursor_at)
        conditions.append(
            or_(
                RagRunFact.accepted_at < database_cursor,
                and_(
                    RagRunFact.accepted_at == database_cursor,
                    RagRunFact.id < cursor_id,
                ),
            )
        )
    return (
        select(RagRunFact)
        .where(*conditions)
        .order_by(RagRunFact.accepted_at.desc(), RagRunFact.id.desc())
        .limit(limit + 1)
    )


def build_error_list_query(
    filters: RagOperationsFilter,
    *,
    cursor: tuple[datetime, UUID] | None,
    limit: int,
) -> Select[tuple[ErrorIssue]]:
    if not 1 <= limit <= 100:
        raise ValueError("operations_limit_invalid")
    conditions = [
        ErrorIssue.last_seen_at >= _database_time(filters.from_at),
        ErrorIssue.last_seen_at < _database_time(filters.to_at),
    ]
    if filters.environment is not None:
        conditions.append(ErrorIssue.environment == filters.environment)
    if filters.release is not None:
        conditions.append(ErrorIssue.last_release == filters.release)
    if filters.org_id is not None:
        occurrence_scope = [
            ErrorOccurrence.issue_id == ErrorIssue.id,
            ErrorOccurrence.org_id == filters.org_id,
        ]
        if filters.workspace_id is not None:
            occurrence_scope.append(ErrorOccurrence.workspace_id == filters.workspace_id)
        conditions.append(exists(select(ErrorOccurrence.id).where(*occurrence_scope)))
    if cursor is not None:
        cursor_at, cursor_id = cursor
        database_cursor = _database_time(cursor_at)
        conditions.append(
            or_(
                ErrorIssue.last_seen_at < database_cursor,
                and_(
                    ErrorIssue.last_seen_at == database_cursor,
                    ErrorIssue.id < cursor_id,
                ),
            )
        )
    return (
        select(ErrorIssue)
        .where(*conditions)
        .order_by(ErrorIssue.last_seen_at.desc(), ErrorIssue.id.desc())
        .limit(limit + 1)
    )


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


async def get_series(
    session: AsyncSession,
    filters: RagOperationsFilter,
    *,
    interval: RagSeriesInterval,
) -> list[RagOperationsSeriesPoint]:
    rows = (await session.execute(build_series_query(filters, interval=interval))).all()
    return [
        RagOperationsSeriesPoint(
            bucket=row.bucket.replace(tzinfo=UTC),
            query_count=int(row.query_count or 0),
            grounded_count=int(row.grounded_count or 0),
            no_answer_count=int(row.no_answer_count or 0),
            failed_count=int(row.failed_count or 0),
            p95_latency_ms=(float(row.p95_latency_ms) if row.p95_latency_ms is not None else None),
        )
        for row in rows
    ]


async def list_runs(
    session: AsyncSession,
    filters: RagOperationsFilter,
    *,
    cursor: tuple[datetime, UUID] | None,
    limit: int,
) -> RagOperationsRunPage:
    rows = list(
        (await session.execute(build_run_list_query(filters, cursor=cursor, limit=limit))).scalars()
    )
    items = rows[:limit]
    next_cursor = (
        encode_operations_cursor(items[-1].accepted_at, items[-1].id)
        if len(rows) > limit and items
        else None
    )
    return RagOperationsRunPage(
        items=[RagOperationsRunOut.model_validate(item) for item in items],
        next_cursor=next_cursor,
    )


async def get_run(session: AsyncSession, run_id: UUID) -> RagOperationsRunOut:
    fact = await session.scalar(select(RagRunFact).where(RagRunFact.run_id == run_id))
    if fact is None:
        raise NotFoundError("RAG run fact not found")
    return RagOperationsRunOut.model_validate(fact)


async def list_errors(
    session: AsyncSession,
    filters: RagOperationsFilter,
    *,
    cursor: tuple[datetime, UUID] | None,
    limit: int,
) -> RagOperationsErrorPage:
    rows = list(
        (
            await session.execute(build_error_list_query(filters, cursor=cursor, limit=limit))
        ).scalars()
    )
    items = rows[:limit]
    next_cursor = (
        encode_operations_cursor(items[-1].last_seen_at, items[-1].id)
        if len(rows) > limit and items
        else None
    )
    return RagOperationsErrorPage(
        items=[ErrorIssueOut.model_validate(item) for item in items],
        next_cursor=next_cursor,
    )


async def get_error(
    session: AsyncSession,
    issue_id: UUID,
) -> RagOperationsErrorDetail:
    issue = await session.get(ErrorIssue, issue_id)
    if issue is None:
        raise NotFoundError("RAG error issue not found")
    occurrences = list(
        (
            await session.execute(
                select(ErrorOccurrence)
                .where(ErrorOccurrence.issue_id == issue_id)
                .order_by(ErrorOccurrence.occurred_at.desc(), ErrorOccurrence.id.desc())
                .limit(100)
            )
        ).scalars()
    )
    return RagOperationsErrorDetail(
        issue=ErrorIssueOut.model_validate(issue),
        occurrences=[ErrorOccurrenceOut.model_validate(item) for item in occurrences],
    )
