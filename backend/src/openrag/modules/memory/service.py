"""Governed memory CRUD with provenance, suppression, and tenant isolation."""

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.db import naive_utc
from openrag.core.errors import ConflictError, NotFoundError
from openrag.modules.audit.service import record_audit
from openrag.modules.memory.models import (
    MemoryPreference,
    MemoryProvenance,
    MemoryRecord,
    MemorySuppression,
)
from openrag.modules.memory.schemas import MemoryCreate, MemoryPatch, MemoryPreferencePatch
from openrag.modules.tenancy import service as tenancy_service
from openrag.modules.tenancy.context import TenantContext

POLICY_VERSION = "explicit-user-memory-v1"
SOURCE_KIND = "explicit_user_action"
SOURCE_TRUST = "explicit_user"


@dataclass(frozen=True, slots=True)
class MemoryPage:
    items: tuple[MemoryRecord, ...]
    provenance: dict[UUID, tuple[MemoryProvenance, ...]]
    next_cursor: str | None


def normalize_canonical_key(value: str) -> str:
    return value.strip().lower()


def normalize_content(value: str) -> str:
    return " ".join(value.split())


def content_digest(content: str, structured_value: dict[str, object] | None) -> str:
    payload = json.dumps(
        {
            "content": normalize_content(content),
            "structured_value": structured_value,
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def suppression_fingerprint(
    *,
    workspace_id: UUID,
    user_id: UUID,
    canonical_key: str,
    content_hash: str,
) -> str:
    payload = "\x1f".join(
        (
            str(workspace_id),
            str(user_id),
            normalize_canonical_key(canonical_key),
            content_hash,
        )
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def encode_memory_cursor(created_at: datetime, memory_id: UUID) -> str:
    payload = json.dumps(
        [created_at.isoformat(), str(memory_id)],
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_memory_cursor(cursor: str) -> tuple[datetime, UUID]:
    if not 1 <= len(cursor) <= 512:
        raise ValueError("memory_cursor_invalid")
    try:
        padding = "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(cursor + padding))
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError
        return datetime.fromisoformat(str(value[0])).replace(tzinfo=None), UUID(str(value[1]))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("memory_cursor_invalid") from exc


def _naive(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


async def _authorize(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
) -> None:
    await tenancy_service.get_workspace(session, context, workspace_id, "chat.use")


async def _provenance_for(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    memory_ids: list[UUID],
) -> dict[UUID, tuple[MemoryProvenance, ...]]:
    if not memory_ids:
        return {}
    rows = list(
        (
            await session.execute(
                select(MemoryProvenance)
                .where(
                    MemoryProvenance.org_id == context.org_id,
                    MemoryProvenance.workspace_id == workspace_id,
                    MemoryProvenance.memory_id.in_(memory_ids),
                )
                .order_by(MemoryProvenance.created_at, MemoryProvenance.id)
            )
        ).scalars()
    )
    grouped: dict[UUID, list[MemoryProvenance]] = {memory_id: [] for memory_id in memory_ids}
    for row in rows:
        grouped[row.memory_id].append(row)
    return {key: tuple(value) for key, value in grouped.items()}


async def _get_memory(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    memory_id: UUID,
    *,
    lock: bool = False,
) -> MemoryRecord:
    await _authorize(session, context, workspace_id)
    statement = select(MemoryRecord).where(
        MemoryRecord.id == memory_id,
        MemoryRecord.org_id == context.org_id,
        MemoryRecord.workspace_id == workspace_id,
        MemoryRecord.user_id == context.user_id,
    )
    if lock:
        statement = statement.with_for_update()
    memory = (await session.execute(statement)).scalar_one_or_none()
    if memory is None:
        raise NotFoundError("memory not found")
    return memory


async def _event_owner(
    session: AsyncSession,
    context: TenantContext,
    event_id: UUID,
) -> MemoryProvenance | None:
    return (
        await session.execute(
            select(MemoryProvenance).where(
                MemoryProvenance.org_id == context.org_id,
                MemoryProvenance.actor_user_id == context.user_id,
                MemoryProvenance.source_event_id == event_id,
            )
        )
    ).scalar_one_or_none()


async def create_memory(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    body: MemoryCreate,
) -> MemoryRecord:
    await _authorize(session, context, workspace_id)
    existing = (
        await session.execute(
            select(MemoryRecord).where(
                MemoryRecord.org_id == context.org_id,
                MemoryRecord.user_id == context.user_id,
                MemoryRecord.client_request_id == body.client_request_id,
            )
        )
    ).scalar_one_or_none()
    content_hash = content_digest(body.content, body.structured_value)
    fingerprint = suppression_fingerprint(
        workspace_id=workspace_id,
        user_id=context.user_id,
        canonical_key=body.canonical_key,
        content_hash=content_hash,
    )
    if existing is not None:
        if (
            existing.workspace_id != workspace_id
            or existing.canonical_key != body.canonical_key
            or existing.content_hash != content_hash
            or existing.memory_type != body.memory_type
            or existing.scope != body.scope
            or existing.confidence != body.confidence
            or existing.importance != body.importance
            or existing.sensitivity != body.sensitivity
            or existing.expires_at != _naive(body.expires_at)
        ):
            raise ConflictError("client request ID was already used")
        return existing
    if await _event_owner(session, context, body.client_request_id) is not None:
        raise ConflictError("client request ID was already used")
    active_key = await session.scalar(
        select(MemoryRecord.id)
        .where(
            MemoryRecord.org_id == context.org_id,
            MemoryRecord.workspace_id == workspace_id,
            MemoryRecord.user_id == context.user_id,
            MemoryRecord.canonical_key == body.canonical_key,
            MemoryRecord.status == "active",
        )
        .with_for_update()
    )
    if active_key is not None:
        raise ConflictError("an active memory with this key already exists; edit it instead")
    suppressed = (
        await session.execute(
            select(MemorySuppression.id).where(
                MemorySuppression.org_id == context.org_id,
                MemorySuppression.workspace_id == workspace_id,
                MemorySuppression.user_id == context.user_id,
                MemorySuppression.fingerprint == fingerprint,
            )
        )
    ).scalar_one_or_none()
    if suppressed is not None:
        raise ConflictError("this memory was previously forgotten")

    memory = MemoryRecord(
        org_id=context.org_id,
        workspace_id=workspace_id,
        user_id=context.user_id,
        client_request_id=body.client_request_id,
        canonical_key=body.canonical_key,
        content=body.content,
        structured_value=body.structured_value,
        memory_type=body.memory_type,
        scope=body.scope,
        status="active",
        confidence=body.confidence,
        importance=body.importance,
        sensitivity=body.sensitivity,
        expires_at=_naive(body.expires_at),
        policy_version=POLICY_VERSION,
        source_trust=SOURCE_TRUST,
        content_hash=content_hash,
        suppression_fingerprint=fingerprint,
    )
    session.add(memory)
    await session.flush()
    session.add(
        MemoryProvenance(
            org_id=context.org_id,
            workspace_id=workspace_id,
            memory_id=memory.id,
            actor_user_id=context.user_id,
            source_kind=SOURCE_KIND,
            source_event_id=body.client_request_id,
            source_hash=content_hash,
        )
    )
    await record_audit(
        session,
        org_id=context.org_id,
        actor_id=context.user_id,
        action="memory.created",
        target_type="memory",
        target_id=str(memory.id),
    )
    await session.commit()
    return memory


async def list_memories(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    *,
    include_history: bool,
    limit: int,
    cursor: str | None,
) -> MemoryPage:
    if not 1 <= limit <= 100:
        raise ValueError("memory_page_limit_invalid")
    await _authorize(session, context, workspace_id)
    statement = select(MemoryRecord).where(
        MemoryRecord.org_id == context.org_id,
        MemoryRecord.workspace_id == workspace_id,
        MemoryRecord.user_id == context.user_id,
    )
    if not include_history:
        statement = statement.where(MemoryRecord.status == "active")
    if cursor is not None:
        cursor_time, cursor_id = decode_memory_cursor(cursor)
        statement = statement.where(
            or_(
                MemoryRecord.created_at < cursor_time,
                and_(MemoryRecord.created_at == cursor_time, MemoryRecord.id < cursor_id),
            )
        )
    rows = list(
        (
            await session.execute(
                statement.order_by(MemoryRecord.created_at.desc(), MemoryRecord.id.desc()).limit(
                    limit + 1
                )
            )
        ).scalars()
    )
    items = rows[:limit]
    next_cursor = (
        encode_memory_cursor(items[-1].created_at, items[-1].id)
        if len(rows) > limit and items
        else None
    )
    return MemoryPage(
        items=tuple(items),
        provenance=await _provenance_for(
            session,
            context,
            workspace_id,
            [item.id for item in items],
        ),
        next_cursor=next_cursor,
    )


async def get_memory(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    memory_id: UUID,
) -> tuple[MemoryRecord, tuple[MemoryProvenance, ...]]:
    memory = await _get_memory(session, context, workspace_id, memory_id)
    provenance = await _provenance_for(session, context, workspace_id, [memory.id])
    return memory, provenance.get(memory.id, ())


async def export_memories(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
) -> tuple[tuple[MemoryRecord, ...], dict[UUID, tuple[MemoryProvenance, ...]], bool]:
    await _authorize(session, context, workspace_id)
    rows = list(
        (
            await session.execute(
                select(MemoryRecord)
                .where(
                    MemoryRecord.org_id == context.org_id,
                    MemoryRecord.workspace_id == workspace_id,
                    MemoryRecord.user_id == context.user_id,
                )
                .order_by(MemoryRecord.created_at, MemoryRecord.id)
                .limit(1001)
            )
        ).scalars()
    )
    items = tuple(rows[:1000])
    provenance = await _provenance_for(
        session,
        context,
        workspace_id,
        [item.id for item in items],
    )
    return items, provenance, len(rows) > 1000


async def update_memory(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    memory_id: UUID,
    body: MemoryPatch,
) -> MemoryRecord:
    await _authorize(session, context, workspace_id)
    owner = await _event_owner(session, context, body.client_request_id)
    if owner is not None:
        if owner.memory_id != memory_id:
            raise ConflictError("client request ID was already used")
        return await _get_memory(session, context, workspace_id, memory_id)
    memory = await _get_memory(session, context, workspace_id, memory_id, lock=True)
    if memory.status != "active":
        raise ConflictError("only active memories can be edited")
    fields = body.model_fields_set
    content = body.content if "content" in fields else memory.content
    structured = (
        body.structured_value if "structured_value" in fields else memory.structured_value
    )
    digest = content_digest(content or "", structured)
    fingerprint = suppression_fingerprint(
        workspace_id=workspace_id,
        user_id=context.user_id,
        canonical_key=memory.canonical_key,
        content_hash=digest,
    )
    suppressed = (
        await session.execute(
            select(MemorySuppression.id).where(
                MemorySuppression.org_id == context.org_id,
                MemorySuppression.workspace_id == workspace_id,
                MemorySuppression.user_id == context.user_id,
                MemorySuppression.fingerprint == fingerprint,
            )
        )
    ).scalar_one_or_none()
    if suppressed is not None:
        raise ConflictError("this memory was previously forgotten")
    if "content" in fields and body.content is not None:
        memory.content = body.content
    if "structured_value" in fields:
        memory.structured_value = body.structured_value
    if "importance" in fields and body.importance is not None:
        memory.importance = body.importance
    if "sensitivity" in fields and body.sensitivity is not None:
        memory.sensitivity = body.sensitivity
    if "expires_at" in fields:
        memory.expires_at = _naive(body.expires_at)
    memory.content_hash = digest
    memory.suppression_fingerprint = fingerprint
    memory.updated_at = naive_utc()
    session.add(
        MemoryProvenance(
            org_id=context.org_id,
            workspace_id=workspace_id,
            memory_id=memory.id,
            actor_user_id=context.user_id,
            source_kind=SOURCE_KIND,
            source_event_id=body.client_request_id,
            source_hash=digest,
        )
    )
    await record_audit(
        session,
        org_id=context.org_id,
        actor_id=context.user_id,
        action="memory.updated",
        target_type="memory",
        target_id=str(memory.id),
    )
    await session.commit()
    return memory


async def forget_memory(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    memory_id: UUID,
    event_id: UUID,
) -> None:
    await _authorize(session, context, workspace_id)
    owner = await _event_owner(session, context, event_id)
    if owner is not None:
        if owner.memory_id != memory_id:
            raise ConflictError("client request ID was already used")
        return
    memory = await _get_memory(session, context, workspace_id, memory_id, lock=True)
    if memory.status == "retracted":
        session.add(
            MemoryProvenance(
                org_id=context.org_id,
                workspace_id=workspace_id,
                memory_id=memory.id,
                actor_user_id=context.user_id,
                source_kind=SOURCE_KIND,
                source_event_id=event_id,
                source_hash=memory.content_hash,
            )
        )
        await session.commit()
        return
    memory.status = "retracted"
    memory.updated_at = naive_utc()
    session.add(
        MemorySuppression(
            org_id=context.org_id,
            workspace_id=workspace_id,
            user_id=context.user_id,
            fingerprint=memory.suppression_fingerprint,
            reason="user_forgot",
        )
    )
    session.add(
        MemoryProvenance(
            org_id=context.org_id,
            workspace_id=workspace_id,
            memory_id=memory.id,
            actor_user_id=context.user_id,
            source_kind=SOURCE_KIND,
            source_event_id=event_id,
            source_hash=memory.content_hash,
        )
    )
    await record_audit(
        session,
        org_id=context.org_id,
        actor_id=context.user_id,
        action="memory.forgotten",
        target_type="memory",
        target_id=str(memory.id),
    )
    await session.commit()


async def get_preferences(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
) -> MemoryPreference:
    await _authorize(session, context, workspace_id)
    preference = (
        await session.execute(
            select(MemoryPreference).where(
                MemoryPreference.org_id == context.org_id,
                MemoryPreference.workspace_id == workspace_id,
                MemoryPreference.user_id == context.user_id,
            )
        )
    ).scalar_one_or_none()
    if preference is None:
        preference = MemoryPreference(
            org_id=context.org_id,
            workspace_id=workspace_id,
            user_id=context.user_id,
            extraction_enabled=False,
            semantic_enabled=True,
            episodic_enabled=False,
            procedural_enabled=False,
            updated_at=naive_utc(),
        )
    return preference


async def update_preferences(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    body: MemoryPreferencePatch,
) -> MemoryPreference:
    await _authorize(session, context, workspace_id)
    preference = (
        await session.execute(
            select(MemoryPreference)
            .where(
                MemoryPreference.org_id == context.org_id,
                MemoryPreference.workspace_id == workspace_id,
                MemoryPreference.user_id == context.user_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if preference is None:
        preference = MemoryPreference(
            org_id=context.org_id,
            workspace_id=workspace_id,
            user_id=context.user_id,
            extraction_enabled=False,
            semantic_enabled=True,
            episodic_enabled=False,
            procedural_enabled=False,
        )
        session.add(preference)
        await session.flush()
    for field in body.model_fields_set:
        setattr(preference, field, getattr(body, field))
    preference.updated_at = naive_utc()
    await record_audit(
        session,
        org_id=context.org_id,
        actor_id=context.user_id,
        action="memory.preferences_updated",
        target_type="memory_preference",
        target_id=str(preference.id),
    )
    await session.commit()
    return preference
