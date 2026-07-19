"""Lease-fenced durable stage coordination without external work in SQL."""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.modules.documents.models import DocumentVersion, IngestStageAttempt

_STAGES = ("parse", "chunk", "embed", "authority_upsert")
_CHECKPOINT = re.compile(
    r"^(parse|chunk|embed|authority_upsert):(ingestion|rebuild):"
    r"([1-9][0-9]{0,7}):([0-9a-f]{32})$"
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,99}$")
_MAX_CANDIDATES = 100
_MAX_ATTEMPTS = 8

AuthorityReady = Callable[[UUID], Awaitable[bool]]
CompleteResult = Literal["advanced", "completed", "lease_lost"]
RetryResult = Literal["queued", "failed", "lease_lost"]


@dataclass(frozen=True, slots=True)
class StageCheckpoint:
    stage: str
    pipeline_kind: str
    pipeline_attempt: int
    authority_generation_id: UUID

    def for_stage(self, stage: str) -> str:
        return (
            f"{stage}:{self.pipeline_kind}:{self.pipeline_attempt}:"
            f"{self.authority_generation_id.hex}"
        )


@dataclass(frozen=True, slots=True)
class StageClaim:
    attempt_id: UUID
    org_id: UUID
    workspace_id: UUID
    document_version_id: UUID
    pipeline_kind: str
    stage: str
    checkpoint: str
    authority_generation_id: UUID
    owner: str
    lease_token: UUID
    lease_expires_at: datetime
    attempt_number: int


def parse_stage_checkpoint(value: str) -> StageCheckpoint:
    matched = _CHECKPOINT.fullmatch(value)
    if matched is None:
        raise ValueError("stage_checkpoint_invalid")
    stage, pipeline_kind, pipeline_attempt, generation = matched.groups()
    return StageCheckpoint(
        stage=stage,
        pipeline_kind=pipeline_kind,
        pipeline_attempt=int(pipeline_attempt),
        authority_generation_id=UUID(hex=generation),
    )


async def _db_now(session: AsyncSession) -> datetime:
    now = await session.scalar(select(func.timezone("UTC", func.now())))
    if not isinstance(now, datetime):
        raise RuntimeError("database_time_unavailable")
    return now


def _claimable(row: IngestStageAttempt, now: datetime) -> bool:
    return row.attempts < _MAX_ATTEMPTS and (
        (row.state == "queued" and row.available_at <= now)
        or (
            row.state == "running"
            and row.lease_expires_at is not None
            and row.lease_expires_at <= now
        )
    )


def _active_version(row: IngestStageAttempt, version: DocumentVersion | None) -> bool:
    if (
        version is None
        or version.source_delete_requested_at is not None
        or version.source_deleted_at is not None
    ):
        return False
    if row.pipeline_kind == "ingestion":
        return version.state == "processing" and version.provenance_state in {
            "none",
            "building",
            "failed",
        }
    return (
        row.pipeline_kind == "rebuild"
        and version.state == "approved"
        and version.provenance_state in {"legacy_pending", "building"}
    )


async def _mark_candidate(
    session_factory: async_sessionmaker[AsyncSession],
    attempt_id: UUID,
    *,
    state: Literal["queued", "failed"],
    error_code: str,
    delay_seconds: int = 0,
) -> None:
    async with session_factory.begin() as session:
        row = await session.scalar(
            select(IngestStageAttempt)
            .where(IngestStageAttempt.id == attempt_id)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return
        now = await _db_now(session)
        if not _claimable(row, now):
            return
        row.state = state
        row.error_code = error_code
        row.lease_owner = None
        row.lease_token = None
        row.lease_expires_at = None
        row.available_at = now + timedelta(seconds=delay_seconds)
        if state == "failed":
            row.finished_at = now


async def _claim_candidate(
    session_factory: async_sessionmaker[AsyncSession],
    attempt_id: UUID,
    *,
    owner: str,
    lease_seconds: int,
) -> StageClaim | None:
    async with session_factory.begin() as session:
        row = await session.scalar(
            select(IngestStageAttempt)
            .where(IngestStageAttempt.id == attempt_id)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        now = await _db_now(session)
        if not _claimable(row, now):
            return None
        try:
            checkpoint = parse_stage_checkpoint(row.checkpoint)
        except ValueError:
            row.state = "failed"
            row.error_code = "STAGE_CHECKPOINT_INVALID"
            row.finished_at = now
            row.lease_owner = None
            row.lease_token = None
            row.lease_expires_at = None
            return None
        if checkpoint.stage != row.stage or checkpoint.pipeline_kind != row.pipeline_kind:
            row.state = "failed"
            row.error_code = "STAGE_CHECKPOINT_INVALID"
            row.finished_at = now
            row.lease_owner = None
            row.lease_token = None
            row.lease_expires_at = None
            return None
        version = await session.scalar(
            select(DocumentVersion)
            .where(
                DocumentVersion.id == row.document_version_id,
                DocumentVersion.org_id == row.org_id,
                DocumentVersion.workspace_id == row.workspace_id,
            )
            .with_for_update()
        )
        if not _active_version(row, version):
            row.state = "cancelled"
            row.error_code = "STALE_VERSION"
            row.finished_at = now
            row.lease_owner = None
            row.lease_token = None
            row.lease_expires_at = None
            return None
        if (
            row.pipeline_kind == "ingestion"
            and row.stage == "parse"
            and version is not None
            and version.provenance_state in {"none", "failed"}
        ):
            version.provenance_state = "building"
            version.processing_error_code = None
        elif (
            row.pipeline_kind == "rebuild"
            and row.stage == "parse"
            and version is not None
            and version.provenance_state == "legacy_pending"
        ):
            version.provenance_state = "building"
            version.processing_error_code = None

        lease_token = uuid4()
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        row.state = "running"
        row.lease_owner = owner
        row.lease_token = lease_token
        row.lease_expires_at = lease_expires_at
        row.attempts += 1
        row.error_code = None
        row.started_at = row.started_at or now
        row.finished_at = None
        return StageClaim(
            attempt_id=row.id,
            org_id=row.org_id,
            workspace_id=row.workspace_id,
            document_version_id=row.document_version_id,
            pipeline_kind=row.pipeline_kind,
            stage=row.stage,
            checkpoint=row.checkpoint,
            authority_generation_id=checkpoint.authority_generation_id,
            owner=owner,
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
            attempt_number=row.attempts,
        )


async def claim_stage(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    owner: str,
    lease_seconds: int = 60,
    authority_ready: AuthorityReady | None = None,
) -> StageClaim | None:
    """Claim one queued/expired stage without holding SQL across readiness I/O."""

    if not 1 <= len(owner) <= 200:
        raise ValueError("stage_owner_invalid")
    if not 5 <= lease_seconds <= 3_600:
        raise ValueError("stage_lease_seconds_invalid")
    async with session_factory() as session:
        now = await _db_now(session)
        candidates = list(
            (
                await session.execute(
                    select(
                        IngestStageAttempt.id,
                        IngestStageAttempt.stage,
                        IngestStageAttempt.checkpoint,
                    )
                    .where(
                        IngestStageAttempt.attempts < _MAX_ATTEMPTS,
                        or_(
                            (
                                (IngestStageAttempt.state == "queued")
                                & (IngestStageAttempt.available_at <= now)
                            ),
                            (
                                (IngestStageAttempt.state == "running")
                                & (IngestStageAttempt.lease_expires_at <= now)
                            ),
                        ),
                    )
                    .order_by(
                        IngestStageAttempt.available_at,
                        IngestStageAttempt.created_at,
                        IngestStageAttempt.id,
                    )
                    .limit(_MAX_CANDIDATES)
                )
            ).all()
        )

    for attempt_id, stage, checkpoint_value in candidates:
        try:
            checkpoint = parse_stage_checkpoint(checkpoint_value)
        except ValueError:
            await _mark_candidate(
                session_factory,
                attempt_id,
                state="failed",
                error_code="STAGE_CHECKPOINT_INVALID",
            )
            continue
        if stage == "authority_upsert":
            ready = False
            if authority_ready is not None:
                try:
                    ready = await authority_ready(checkpoint.authority_generation_id)
                except Exception:  # noqa: BLE001 - authority readiness fails closed
                    ready = False
            if not ready:
                await _mark_candidate(
                    session_factory,
                    attempt_id,
                    state="queued",
                    error_code="AUTHORITY_STORAGE_NOT_READY",
                    delay_seconds=30,
                )
                continue
        claim = await _claim_candidate(
            session_factory,
            attempt_id,
            owner=owner,
            lease_seconds=lease_seconds,
        )
        if claim is not None:
            return claim
    return None


async def complete_stage(
    session_factory: async_sessionmaker[AsyncSession],
    claim: StageClaim,
    *,
    output_digest: str,
) -> CompleteResult:
    """Fence completion and atomically create the next queued stage."""

    if _DIGEST.fullmatch(output_digest) is None:
        raise ValueError("stage_output_digest_invalid")
    async with session_factory.begin() as session:
        row = await session.scalar(
            select(IngestStageAttempt)
            .where(
                IngestStageAttempt.id == claim.attempt_id,
                IngestStageAttempt.lease_token == claim.lease_token,
                IngestStageAttempt.lease_owner == claim.owner,
                IngestStageAttempt.state == "running",
            )
            .with_for_update()
        )
        now = await _db_now(session)
        if row is None or row.lease_expires_at is None or row.lease_expires_at <= now:
            return "lease_lost"
        checkpoint = parse_stage_checkpoint(row.checkpoint)
        row.state = "succeeded"
        row.output_digest = output_digest
        row.error_code = None
        row.finished_at = now
        row.lease_owner = None
        row.lease_token = None
        row.lease_expires_at = None

        stage_index = _STAGES.index(row.stage)
        if stage_index == len(_STAGES) - 1:
            return "completed"
        next_stage = _STAGES[stage_index + 1]
        await session.execute(
            insert(IngestStageAttempt)
            .values(
                id=uuid4(),
                created_at=now,
                org_id=row.org_id,
                workspace_id=row.workspace_id,
                document_version_id=row.document_version_id,
                pipeline_kind=row.pipeline_kind,
                stage=next_stage,
                state="queued",
                checkpoint=checkpoint.for_stage(next_stage),
                attempts=0,
                available_at=now,
            )
            .on_conflict_do_nothing(
                constraint="uq_ingest_stage_attempts_checkpoint"
            )
        )
        return "advanced"


async def heartbeat_stage(
    session_factory: async_sessionmaker[AsyncSession],
    claim: StageClaim,
    *,
    lease_seconds: int,
) -> bool:
    """Extend only the still-current, unexpired lease token."""

    if not 5 <= lease_seconds <= 3_600:
        raise ValueError("stage_lease_seconds_invalid")
    async with session_factory.begin() as session:
        row = await session.scalar(
            select(IngestStageAttempt)
            .where(
                IngestStageAttempt.id == claim.attempt_id,
                IngestStageAttempt.lease_token == claim.lease_token,
                IngestStageAttempt.lease_owner == claim.owner,
                IngestStageAttempt.state == "running",
            )
            .with_for_update()
        )
        now = await _db_now(session)
        if row is None or row.lease_expires_at is None or row.lease_expires_at <= now:
            return False
        row.lease_expires_at = now + timedelta(seconds=lease_seconds)
        return True


async def retry_stage(
    session_factory: async_sessionmaker[AsyncSession],
    claim: StageClaim,
    *,
    error_code: str,
    terminal: bool = False,
) -> RetryResult:
    """Fence failure, then retry with bounded backoff or terminalize."""

    if _ERROR_CODE.fullmatch(error_code) is None:
        raise ValueError("stage_error_code_invalid")
    async with session_factory.begin() as session:
        row = await session.scalar(
            select(IngestStageAttempt)
            .where(
                IngestStageAttempt.id == claim.attempt_id,
                IngestStageAttempt.lease_token == claim.lease_token,
                IngestStageAttempt.lease_owner == claim.owner,
                IngestStageAttempt.state == "running",
            )
            .with_for_update()
        )
        now = await _db_now(session)
        if row is None or row.lease_expires_at is None or row.lease_expires_at <= now:
            return "lease_lost"
        failed = terminal or row.attempts >= _MAX_ATTEMPTS
        row.state = "failed" if failed else "queued"
        row.error_code = error_code
        row.lease_owner = None
        row.lease_token = None
        row.lease_expires_at = None
        if failed:
            row.finished_at = now
            version = await session.scalar(
                select(DocumentVersion)
                .where(
                    DocumentVersion.id == row.document_version_id,
                    DocumentVersion.org_id == row.org_id,
                    DocumentVersion.workspace_id == row.workspace_id,
                )
                .with_for_update()
            )
            if version is not None:
                if row.pipeline_kind == "ingestion":
                    version.state = "failed"
                version.provenance_state = "failed"
                version.processing_error_code = error_code
        else:
            row.available_at = now + timedelta(
                seconds=min(2 ** row.attempts, 300)
            )
        return "failed" if failed else "queued"
