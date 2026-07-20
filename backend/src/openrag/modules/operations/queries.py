"""Bounded database-side read models for platform RAG operations."""

import base64
import binascii
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Subquery

from openrag.core.errors import NotFoundError
from openrag.modules.chat.models import Message
from openrag.modules.chat.quality_models import AnswerQualityAudit
from openrag.modules.operations.models import ErrorIssue, ErrorOccurrence, RagRunFact
from openrag.modules.operations.schemas import (
    AnswerQualityFilter,
    AnswerQualityOverview,
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


def _answer_quality_conditions(
    filters: AnswerQualityFilter,
) -> list[ColumnElement[bool]]:
    conditions = [
        AnswerQualityAudit.created_at >= _database_time(filters.from_at),
        AnswerQualityAudit.created_at < _database_time(filters.to_at),
    ]
    for column, value in (
        (AnswerQualityAudit.org_id, filters.org_id),
        (AnswerQualityAudit.workspace_id, filters.workspace_id),
        (Message.model_id, filters.model_id),
    ):
        if value is not None:
            conditions.append(column == value)
    return conditions


def build_answer_quality_overview_query(
    filters: AnswerQualityFilter,
) -> Select[tuple[object, ...]]:
    return (
        select(
            func.count().label("scheduled_count"),
            func.count()
            .filter(AnswerQualityAudit.status == "completed")
            .label("completed_count"),
            func.count()
            .filter(
                AnswerQualityAudit.status == "completed",
                AnswerQualityAudit.passed.is_(True),
            )
            .label("passed_count"),
            func.count()
            .filter(
                AnswerQualityAudit.status == "completed",
                AnswerQualityAudit.passed.is_(False),
            )
            .label("rejected_count"),
            func.count()
            .filter(AnswerQualityAudit.status.in_(("queued", "running")))
            .label("pending_count"),
            func.count()
            .filter(AnswerQualityAudit.status == "skipped")
            .label("skipped_count"),
            func.count()
            .filter(AnswerQualityAudit.status == "failed")
            .label("worker_failed_count"),
            func.avg(AnswerQualityAudit.grounding_score).label(
                "average_grounding_score"
            ),
            func.avg(AnswerQualityAudit.completeness_score).label(
                "average_completeness_score"
            ),
        )
        .join(
            Message,
            and_(
                Message.org_id == AnswerQualityAudit.org_id,
                Message.workspace_id == AnswerQualityAudit.workspace_id,
                Message.id == AnswerQualityAudit.message_id,
            ),
        )
        .where(*_answer_quality_conditions(filters))
    )


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


def build_run_detail_query(
    run_id: UUID,
    filters: RagOperationsFilter,
) -> Select[tuple[RagRunFact]]:
    return select(RagRunFact).where(
        RagRunFact.run_id == run_id,
        *_fact_conditions(filters),
    )


def _error_occurrence_conditions(filters: RagOperationsFilter) -> list[ColumnElement[bool]]:
    conditions = [
        ErrorOccurrence.occurred_at >= _database_time(filters.from_at),
        ErrorOccurrence.occurred_at < _database_time(filters.to_at),
    ]
    if filters.org_id is not None:
        conditions.append(ErrorOccurrence.org_id == filters.org_id)
    if filters.workspace_id is not None:
        conditions.append(ErrorOccurrence.workspace_id == filters.workspace_id)
    if filters.release is not None:
        conditions.append(ErrorOccurrence.release == filters.release)
    return conditions


def _scoped_error_occurrences(filters: RagOperationsFilter) -> Subquery:
    return (
        select(
            ErrorOccurrence.issue_id.label("issue_id"),
            func.count(ErrorOccurrence.id).label("occurrence_count"),
            func.min(ErrorOccurrence.occurred_at).label("first_seen_at"),
            func.max(ErrorOccurrence.occurred_at).label("last_seen_at"),
        )
        .where(*_error_occurrence_conditions(filters))
        .group_by(ErrorOccurrence.issue_id)
        .subquery("scoped_error_occurrences")
    )


def build_error_list_query(
    filters: RagOperationsFilter,
    *,
    cursor: tuple[datetime, UUID] | None,
    limit: int,
) -> Select[tuple[ErrorIssue, int, datetime, datetime]]:
    if not 1 <= limit <= 100:
        raise ValueError("operations_limit_invalid")
    scope = _scoped_error_occurrences(filters)
    conditions: list[ColumnElement[bool]] = []
    if filters.environment is not None:
        conditions.append(ErrorIssue.environment == filters.environment)
    if cursor is not None:
        cursor_at, cursor_id = cursor
        database_cursor = _database_time(cursor_at)
        conditions.append(
            or_(
                scope.c.last_seen_at < database_cursor,
                and_(
                    scope.c.last_seen_at == database_cursor,
                    ErrorIssue.id < cursor_id,
                ),
            )
        )
    return (
        select(
            ErrorIssue,
            scope.c.occurrence_count,
            scope.c.first_seen_at,
            scope.c.last_seen_at,
        )
        .join(scope, scope.c.issue_id == ErrorIssue.id)
        .where(*conditions)
        .order_by(scope.c.last_seen_at.desc(), ErrorIssue.id.desc())
        .limit(limit + 1)
    )


def build_error_issue_detail_query(
    issue_id: UUID,
    filters: RagOperationsFilter,
) -> Select[tuple[ErrorIssue, int, datetime, datetime]]:
    scope = _scoped_error_occurrences(filters)
    conditions = [ErrorIssue.id == issue_id]
    if filters.environment is not None:
        conditions.append(ErrorIssue.environment == filters.environment)
    return (
        select(
            ErrorIssue,
            scope.c.occurrence_count,
            scope.c.first_seen_at,
            scope.c.last_seen_at,
        )
        .join(scope, scope.c.issue_id == ErrorIssue.id)
        .where(*conditions)
    )


def build_error_occurrence_detail_query(
    issue_id: UUID,
    filters: RagOperationsFilter,
) -> Select[tuple[ErrorOccurrence]]:
    conditions = [
        ErrorOccurrence.issue_id == issue_id,
        *_error_occurrence_conditions(filters),
    ]
    return (
        select(ErrorOccurrence)
        .where(*conditions)
        .order_by(ErrorOccurrence.occurred_at.desc(), ErrorOccurrence.id.desc())
        .limit(100)
    )


def scoped_error_issue_out(
    issue: ErrorIssue,
    *,
    occurrence_count: int,
    first_seen_at: datetime,
    last_seen_at: datetime,
) -> ErrorIssueOut:
    values = ErrorIssueOut.model_validate(issue).model_dump()
    values.update(
        occurrence_count=occurrence_count,
        first_seen_at=first_seen_at,
        last_seen_at=last_seen_at,
    )
    return ErrorIssueOut.model_validate(values)


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


async def get_answer_quality_overview(
    session: AsyncSession,
    filters: AnswerQualityFilter,
) -> AnswerQualityOverview:
    row = (await session.execute(build_answer_quality_overview_query(filters))).one()
    scheduled_count = int(row.scheduled_count or 0)
    completed_count = int(row.completed_count or 0)
    passed_count = int(row.passed_count or 0)
    return AnswerQualityOverview(
        scheduled_count=scheduled_count,
        completed_count=completed_count,
        passed_count=passed_count,
        rejected_count=int(row.rejected_count or 0),
        pending_count=int(row.pending_count or 0),
        skipped_count=int(row.skipped_count or 0),
        worker_failed_count=int(row.worker_failed_count or 0),
        completion_rate=(
            completed_count / scheduled_count if scheduled_count else 0.0
        ),
        pass_rate=passed_count / completed_count if completed_count else 0.0,
        average_grounding_score=(
            float(row.average_grounding_score)
            if row.average_grounding_score is not None
            else None
        ),
        average_completeness_score=(
            float(row.average_completeness_score)
            if row.average_completeness_score is not None
            else None
        ),
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


async def get_run(
    session: AsyncSession,
    run_id: UUID,
    filters: RagOperationsFilter,
) -> RagOperationsRunOut:
    fact = await session.scalar(build_run_detail_query(run_id, filters))
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
        (await session.execute(build_error_list_query(filters, cursor=cursor, limit=limit))).all()
    )
    scoped_rows = rows[:limit]
    items = [
        scoped_error_issue_out(
            issue,
            occurrence_count=int(occurrence_count),
            first_seen_at=first_seen_at,
            last_seen_at=last_seen_at,
        )
        for issue, occurrence_count, first_seen_at, last_seen_at in scoped_rows
    ]
    next_cursor = (
        encode_operations_cursor(items[-1].last_seen_at, items[-1].id)
        if len(rows) > limit and items
        else None
    )
    return RagOperationsErrorPage(
        items=items,
        next_cursor=next_cursor,
    )


async def get_error(
    session: AsyncSession,
    issue_id: UUID,
    filters: RagOperationsFilter,
) -> RagOperationsErrorDetail:
    scoped_issue = (
        await session.execute(build_error_issue_detail_query(issue_id, filters))
    ).one_or_none()
    if scoped_issue is None:
        raise NotFoundError("RAG error issue not found")
    issue, occurrence_count, first_seen_at, last_seen_at = scoped_issue
    occurrences = list(
        (
            await session.execute(
                build_error_occurrence_detail_query(issue_id, filters)
            )
        ).scalars()
    )
    return RagOperationsErrorDetail(
        issue=scoped_error_issue_out(
            issue,
            occurrence_count=int(occurrence_count),
            first_seen_at=first_seen_at,
            last_seen_at=last_seen_at,
        ),
        occurrences=[ErrorOccurrenceOut.model_validate(item) for item in occurrences],
    )
