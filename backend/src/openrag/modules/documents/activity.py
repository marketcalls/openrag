"""Tenant-safe document processing and governance read models."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import get_settings
from openrag.modules.documents.models import (
    DocumentBlock,
    DocumentVersionDecisionRecord,
    IngestStageAttempt,
)
from openrag.modules.documents.schemas import (
    DocumentOcrSummaryOut,
    DocumentStageAttemptOut,
    DocumentVersionActivityOut,
    DocumentVersionDecisionOut,
)
from openrag.modules.documents.service import get_version_checked
from openrag.modules.tenancy.context import TenantContext

_MAX_ACTIVITY_ROWS = 100


async def get_version_activity(
    session: AsyncSession,
    context: TenantContext,
    version_id: UUID,
) -> DocumentVersionActivityOut:
    """Return bounded operational history without source text or storage identity."""

    version = await get_version_checked(session, context, version_id)
    decisions = list(
        (
            await session.scalars(
                select(DocumentVersionDecisionRecord)
                .where(
                    DocumentVersionDecisionRecord.org_id == context.org_id,
                    DocumentVersionDecisionRecord.document_version_id == version.id,
                )
                .order_by(
                    DocumentVersionDecisionRecord.lifecycle_revision,
                    DocumentVersionDecisionRecord.created_at,
                )
                .limit(_MAX_ACTIVITY_ROWS)
            )
        ).all()
    )
    stages = list(
        (
            await session.scalars(
                select(IngestStageAttempt)
                .where(
                    IngestStageAttempt.org_id == context.org_id,
                    IngestStageAttempt.document_version_id == version.id,
                )
                .order_by(IngestStageAttempt.created_at, IngestStageAttempt.id)
                .limit(_MAX_ACTIVITY_ROWS)
            )
        ).all()
    )
    threshold = get_settings().ocr_min_confidence
    detected_pages, low_confidence_pages = (
        await session.execute(
            select(
                func.count(func.distinct(DocumentBlock.page_number)).filter(
                    DocumentBlock.ocr_confidence.is_not(None)
                ),
                func.count(func.distinct(DocumentBlock.page_number)).filter(
                    DocumentBlock.ocr_confidence.is_not(None),
                    DocumentBlock.ocr_confidence < threshold,
                ),
            ).where(
                DocumentBlock.org_id == context.org_id,
                DocumentBlock.document_version_id == version.id,
            )
        )
    ).one()
    return DocumentVersionActivityOut(
        version_id=version.id,
        decisions=[DocumentVersionDecisionOut.from_record(row) for row in decisions],
        stages=[DocumentStageAttemptOut.from_attempt(row) for row in stages],
        ocr=DocumentOcrSummaryOut(
            detected_pages=detected_pages or 0,
            low_confidence_pages=low_confidence_pages or 0,
        ),
    )
