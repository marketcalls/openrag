"""Tenant-safe persistence and retrieval for validated message artifacts."""

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import Select, and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.errors import ConflictError, InvalidRequestError, NotFoundError
from openrag.modules.artifacts.models import MessageArtifact
from openrag.modules.artifacts.schemas import (
    AnalyticsResponseV1,
    MessageArtifactOut,
)
from openrag.modules.chat.models import Chat, Citation, Message
from openrag.modules.tenancy.context import TenantContext

ARTIFACT_KIND = "analytics"
MAX_ARTIFACT_MESSAGE_IDS = 500


@dataclass(frozen=True, slots=True)
class SerializedAnalyticsArtifact:
    payload: dict[str, object]
    encoded: bytes
    content_hash: str


def serialize_analytics_artifact(
    artifact: AnalyticsResponseV1,
) -> SerializedAnalyticsArtifact:
    """Return stable JSON bytes and their SHA-256 identity."""

    payload = artifact.model_dump(mode="json")
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return SerializedAnalyticsArtifact(
        payload=payload,
        encoded=encoded,
        content_hash=hashlib.sha256(encoded).hexdigest(),
    )


def analytics_source_markers(artifact: AnalyticsResponseV1) -> frozenset[int]:
    markers: set[int] = set()
    for kpi in artifact.kpis:
        markers.update(kpi.source_markers)
    for block in artifact.blocks:
        markers.update(block.source_markers)
    return frozenset(markers)


def _owned_message_statement(
    context: TenantContext,
    message_id: UUID,
) -> Select[tuple[Message]]:
    return (
        select(Message)
        .join(
            Chat,
            and_(
                Chat.id == Message.chat_id,
                Chat.org_id == Message.org_id,
                Chat.workspace_id == Message.workspace_id,
            ),
        )
        .where(
            Message.id == message_id,
            Message.org_id == context.org_id,
            Chat.user_id == context.user_id,
        )
    )


async def _existing_artifact(
    session: AsyncSession,
    *,
    org_id: UUID,
    workspace_id: UUID,
    message_id: UUID,
) -> MessageArtifact | None:
    return (
        await session.execute(
            select(MessageArtifact).where(
                MessageArtifact.org_id == org_id,
                MessageArtifact.workspace_id == workspace_id,
                MessageArtifact.message_id == message_id,
                MessageArtifact.kind == ARTIFACT_KIND,
            )
        )
    ).scalar_one_or_none()


def _resolve_idempotent(
    existing: MessageArtifact,
    serialized: SerializedAnalyticsArtifact,
) -> MessageArtifact:
    if (
        existing.schema_version != "analytics.v1"
        or existing.content_hash != serialized.content_hash
        or existing.payload != serialized.payload
    ):
        raise ConflictError("analytics artifact already exists for this message")
    return existing


async def persist_analytics_artifact(
    session: AsyncSession,
    context: TenantContext,
    message_id: UUID,
    artifact: AnalyticsResponseV1,
) -> MessageArtifact:
    """Persist one citation-bound analytics artifact without committing its turn."""

    message = (
        await session.execute(_owned_message_statement(context, message_id))
    ).scalar_one_or_none()
    if message is None:
        raise NotFoundError("message not found")
    if message.role != "assistant" or message.answer_status not in {
        "grounded",
        "cited_conflict",
    }:
        raise InvalidRequestError("analytics artifacts require a grounded assistant message")

    allowed_markers = frozenset(
        (
            await session.execute(
                select(Citation.marker).where(
                    Citation.org_id == context.org_id,
                    Citation.workspace_id == message.workspace_id,
                    Citation.message_id == message.id,
                )
            )
        ).scalars()
    )
    required_markers = analytics_source_markers(artifact)
    if not required_markers or not required_markers.issubset(allowed_markers):
        raise InvalidRequestError(
            "analytics artifact references an unavailable citation marker"
        )

    serialized = serialize_analytics_artifact(artifact)
    existing = await _existing_artifact(
        session,
        org_id=context.org_id,
        workspace_id=message.workspace_id,
        message_id=message.id,
    )
    if existing is not None:
        return _resolve_idempotent(existing, serialized)

    row = MessageArtifact(
        org_id=context.org_id,
        workspace_id=message.workspace_id,
        message_id=message.id,
        kind=ARTIFACT_KIND,
        schema_version=artifact.schema_version,
        payload=serialized.payload,
        content_hash=serialized.content_hash,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        concurrent = await _existing_artifact(
            session,
            org_id=context.org_id,
            workspace_id=message.workspace_id,
            message_id=message.id,
        )
        if concurrent is None:
            raise
        return _resolve_idempotent(concurrent, serialized)
    return row


def _strict_output(row: MessageArtifact) -> MessageArtifactOut | None:
    try:
        artifact = AnalyticsResponseV1.model_validate(row.payload)
    except ValidationError:
        return None
    serialized = serialize_analytics_artifact(artifact)
    if serialized.content_hash != row.content_hash:
        return None
    return MessageArtifactOut(
        id=row.id,
        message_id=row.message_id,
        kind="analytics",
        schema_version="analytics.v1",
        artifact=artifact,
        content_hash=row.content_hash,
        created_at=row.created_at,
    )


async def list_message_artifacts(
    session: AsyncSession,
    context: TenantContext,
    message_ids: Iterable[UUID],
) -> dict[UUID, tuple[MessageArtifactOut, ...]]:
    """Load artifacts for owned messages in one bounded, tenant-scoped query."""

    bounded_ids = tuple(dict.fromkeys(message_ids))
    if len(bounded_ids) > MAX_ARTIFACT_MESSAGE_IDS:
        raise ValueError("artifact message list exceeds limit")
    if not bounded_ids:
        return {}

    statement = (
        select(MessageArtifact)
        .join(
            Message,
            and_(
                Message.id == MessageArtifact.message_id,
                Message.org_id == MessageArtifact.org_id,
                Message.workspace_id == MessageArtifact.workspace_id,
            ),
        )
        .join(
            Chat,
            and_(
                Chat.id == Message.chat_id,
                Chat.org_id == Message.org_id,
                Chat.workspace_id == Message.workspace_id,
            ),
        )
        .where(
            MessageArtifact.org_id == context.org_id,
            MessageArtifact.message_id.in_(bounded_ids),
            Chat.user_id == context.user_id,
        )
        .order_by(MessageArtifact.created_at, MessageArtifact.id)
    )
    grouped: defaultdict[UUID, list[MessageArtifactOut]] = defaultdict(list)
    for row in (await session.execute(statement)).scalars():
        output = _strict_output(row)
        if output is not None:
            grouped[row.message_id].append(output)
    return {message_id: tuple(items) for message_id, items in grouped.items()}
