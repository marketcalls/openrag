"""Content-free error fingerprinting and atomic issue occurrence recording."""

import hashlib
import traceback
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import case
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.db import naive_utc
from openrag.modules.operations.models import ErrorIssue, ErrorOccurrence
from openrag.modules.operations.schemas import ErrorOccurrenceCreate


def error_fingerprint(occurrence: ErrorOccurrenceCreate) -> str:
    payload = "\x1f".join(
        (
            occurrence.category,
            occurrence.code,
            occurrence.service,
            occurrence.exception_type,
            occurrence.top_frame or "",
        )
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def top_application_frame(exc: BaseException) -> str | None:
    frames = traceback.extract_tb(exc.__traceback__)
    for frame in reversed(frames):
        if "site-packages" in frame.filename:
            continue
        filename = Path(frame.filename).name[:120]
        function = frame.name[:120]
        return f"{filename}:{function}:{frame.lineno}"[:300]
    return None


def _database_time(value: datetime | None) -> datetime:
    if value is None:
        return naive_utc()
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


async def record_error(
    session_factory: async_sessionmaker[AsyncSession],
    occurrence: ErrorOccurrenceCreate,
) -> UUID:
    """Upsert one issue and append one safe occurrence in a short transaction."""

    occurred_at = _database_time(occurrence.occurred_at)
    fingerprint = error_fingerprint(occurrence)
    async with session_factory.begin() as session:
        issue_id = await session.scalar(
            insert(ErrorIssue)
            .values(
                fingerprint=fingerprint,
                category=occurrence.category,
                code=occurrence.code,
                service=occurrence.service,
                environment=occurrence.environment,
                exception_type=occurrence.exception_type,
                top_frame=occurrence.top_frame,
                first_release=occurrence.release,
                last_release=occurrence.release,
                occurrence_count=1,
                first_seen_at=occurred_at,
                last_seen_at=occurred_at,
            )
            .on_conflict_do_update(
                index_elements=[
                    ErrorIssue.environment,
                    ErrorIssue.service,
                    ErrorIssue.fingerprint,
                ],
                set_={
                    "last_seen_at": occurred_at,
                    "last_release": occurrence.release,
                    "occurrence_count": ErrorIssue.occurrence_count + 1,
                    "status": case(
                        (ErrorIssue.status == "ignored", "ignored"),
                        else_="open",
                    ),
                    "resolved_at": None,
                },
            )
            .returning(ErrorIssue.id)
        )
        if issue_id is None:
            raise RuntimeError("error_issue_upsert_failed")
        session.add(
            ErrorOccurrence(
                issue_id=issue_id,
                org_id=occurrence.org_id,
                workspace_id=occurrence.workspace_id,
                run_id=occurrence.run_id,
                trace_id=occurrence.trace_id,
                code=occurrence.code,
                exception_type=occurrence.exception_type,
                http_method=occurrence.http_method,
                route_template=occurrence.route_template,
                http_status=occurrence.http_status,
                release=occurrence.release,
                occurred_at=occurred_at,
            )
        )
    return issue_id
