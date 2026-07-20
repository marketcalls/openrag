"""Bounded oldest-work projections for durable OpenRAG queues."""

from datetime import datetime

from sqlalchemy import func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import CompoundSelect

from openrag.core.db import naive_utc
from openrag.modules.chat.summary_models import ConversationSummaryJob
from openrag.modules.documents.models import DocumentVersionProjection, IngestStageAttempt
from openrag.modules.evaluations.models import EvaluationRun
from openrag.modules.events.models import OutboxEvent
from openrag.modules.runs.models import AgentRun


def build_queue_age_query(
    now: datetime,
) -> CompoundSelect[tuple[str, datetime | None]]:
    return union_all(
        select(
            literal("runs").label("queue"),
            func.min(AgentRun.accepted_at).label("oldest_at"),
        ).where(AgentRun.status.in_(("accepted", "queued"))),
        select(
            literal("ingestion").label("queue"),
            func.min(IngestStageAttempt.created_at).label("oldest_at"),
        ).where(
            IngestStageAttempt.state == "queued",
            IngestStageAttempt.available_at <= now,
        ),
        select(
            literal("summaries").label("queue"),
            func.min(ConversationSummaryJob.created_at).label("oldest_at"),
        ).where(ConversationSummaryJob.status == "queued"),
        select(
            literal("evaluations").label("queue"),
            func.min(EvaluationRun.created_at).label("oldest_at"),
        ).where(EvaluationRun.status == "queued"),
        select(
            literal("outbox").label("queue"),
            func.min(OutboxEvent.created_at).label("oldest_at"),
        ).where(
            OutboxEvent.published_at.is_(None),
            OutboxEvent.dead_lettered_at.is_(None),
            OutboxEvent.dispatch_after <= now,
        ),
        select(
            literal("embeddings").label("queue"),
            func.min(DocumentVersionProjection.created_at).label("oldest_at"),
        ).where(
            DocumentVersionProjection.sync_state.in_(("queued", "retry")),
            DocumentVersionProjection.sync_available_at <= now,
        ),
    )


async def collect_queue_ages(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> dict[str, float]:
    measured_at = now or naive_utc()
    rows = (await session.execute(build_queue_age_query(measured_at))).all()
    return {
        str(row.queue): (
            max(0.0, (measured_at - row.oldest_at).total_seconds())
            if row.oldest_at is not None
            else 0.0
        )
        for row in rows
    }
