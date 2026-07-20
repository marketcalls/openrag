"""Translate the existing reply stream into bounded durable public events."""

import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Literal, Protocol
from uuid import UUID, uuid5

from openrag.modules.chat.events import SSEEvent
from openrag.modules.runs.events import RunEventEnvelope, RunEventType
from openrag.modules.runs.lifecycle import RunIdentity

RunOutcome = Literal["completed", "failed", "cancelled"]
_SOURCE_FIELDS = (
    "marker",
    "document_id",
    "filename",
    "document_version_id",
    "evidence_span_id",
    "version_label",
    "section_label",
    "section_path",
    "locator_kind",
    "locator_label",
    "page",
    "chunk_index",
    "score",
    "dense_score",
    "sparse_score",
    "fused_score",
    "rerank_score",
)
_CITATION_FIELDS = (
    "marker",
    "document_id",
    "chunk_ref",
    "document_version_id",
    "evidence_span_id",
    "document_name",
    "version_label",
    "section_label",
    "section_path",
    "locator_kind",
    "locator_label",
    "page",
    "score",
)


class ReplyLifecycle(Protocol):
    async def is_cancel_requested(self, run_id: UUID) -> bool: ...

    async def first_token(self, run_id: UUID) -> bool: ...

    async def complete(
        self,
        run_id: UUID,
        *,
        assistant_message_id: UUID | None,
        usage: tuple[int, int],
    ) -> bool: ...

    async def fail(self, run_id: UUID, *, error_code: str) -> bool: ...

    async def acknowledge_cancel(self, run_id: UUID) -> bool: ...


class ReplyEventBus(Protocol):
    async def append(
        self,
        *,
        event_type: RunEventType,
        run_id: UUID,
        org_id: UUID,
        workspace_id: UUID,
        chat_id: UUID,
        payload: dict[str, object],
        event_id: UUID | None = None,
    ) -> RunEventEnvelope: ...


class ReplyStageObserver(Protocol):
    def route_selected(self) -> None: ...

    def retrieval_started(self) -> None: ...

    def retrieval_completed(self) -> None: ...

    def first_token(self) -> None: ...

    def persistence_started(self) -> None: ...

    def persistence_completed(self) -> None: ...


def _safe_records(
    value: object,
    fields: tuple[str, ...],
) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    records: list[dict[str, object]] = []
    for raw in value[:32]:
        if not isinstance(raw, Mapping):
            continue
        record: dict[str, object] = {}
        for field in fields:
            field_value = raw.get(field)
            if (
                field_value is not None
                and isinstance(field_value, str | int | float | list)
                and not isinstance(field_value, bool)
            ):
                record[field] = field_value
        records.append(record)
    return records


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("reply_event_contract_invalid")
    return value


class DurableReplyBridge:
    """Make one reply replayable without publishing prompts or raw errors."""

    def __init__(
        self,
        lifecycle: ReplyLifecycle,
        bus: ReplyEventBus,
        *,
        on_route: Callable[[str], Awaitable[None]] | None = None,
        stage_observer: ReplyStageObserver | None = None,
    ) -> None:
        self._lifecycle = lifecycle
        self._bus = bus
        self._on_route = on_route
        self._stage_observer = stage_observer

    async def _persist[T](self, operation: Awaitable[T]) -> T:
        if self._stage_observer is not None:
            self._stage_observer.persistence_started()
        try:
            return await operation
        finally:
            if self._stage_observer is not None:
                self._stage_observer.persistence_completed()

    async def _append(
        self,
        identity: RunIdentity,
        event_type: RunEventType,
        payload: dict[str, object],
        *,
        dedupe_key: str,
    ) -> None:
        await self._bus.append(
            event_type=event_type,
            run_id=identity.run_id,
            org_id=identity.org_id,
            workspace_id=identity.workspace_id,
            chat_id=identity.chat_id,
            payload=payload,
            event_id=uuid5(
                identity.run_id,
                f"openrag-reply-event:{dedupe_key}",
            ),
        )

    async def consume(
        self,
        identity: RunIdentity,
        events: AsyncIterator[SSEEvent],
    ) -> RunOutcome:
        first_token_seen = False
        delta_index = 0
        citations: list[dict[str, object]] = []
        async for event in events:
            # A persisted `done` frame must race as completion, otherwise a late
            # cancel could leave an already-committed assistant message orphaned.
            if event.event != "done" and await self._lifecycle.is_cancel_requested(identity.run_id):
                await self._persist(self._lifecycle.acknowledge_cancel(identity.run_id))
                return "cancelled"

            if event.event == "route_selected":
                route = event.data.get("route")
                reason = event.data.get("reason_code")
                if not isinstance(route, str) or not isinstance(reason, str):
                    await self._persist(
                        self._lifecycle.fail(identity.run_id, error_code="internal")
                    )
                    return "failed"
                if self._stage_observer is not None:
                    self._stage_observer.route_selected()
                if self._on_route is not None:
                    await self._on_route(route[:32])
                await self._append(
                    identity,
                    "route.selected",
                    {"route": route[:32], "reason_code": reason[:100]},
                    dedupe_key="route.selected",
                )
            elif event.event == "retrieval_started":
                if self._stage_observer is not None:
                    self._stage_observer.retrieval_started()
                await self._append(
                    identity,
                    "retrieval.started",
                    {},
                    dedupe_key="retrieval.started",
                )
            elif event.event == "sources":
                sources = _safe_records(event.data.get("sources"), _SOURCE_FIELDS)
                if self._stage_observer is not None:
                    self._stage_observer.retrieval_completed()
                await self._append(
                    identity,
                    "retrieval.sources",
                    {"sources": sources},
                    dedupe_key="retrieval.sources",
                )
                await self._append(
                    identity,
                    "retrieval.completed",
                    {"source_count": len(sources)},
                    dedupe_key="retrieval.completed",
                )
            elif event.event == "token":
                delta = event.data.get("delta")
                if not isinstance(delta, str) or not delta:
                    continue
                if not first_token_seen:
                    if self._stage_observer is not None:
                        self._stage_observer.first_token()
                    await self._lifecycle.first_token(identity.run_id)
                    first_token_seen = True
                for start in range(0, len(delta), 16_000):
                    part = delta[start : start + 16_000]
                    digest = hashlib.sha256(part.encode("utf-8")).hexdigest()
                    await self._append(
                        identity,
                        "message.delta",
                        {"delta": part},
                        dedupe_key=f"message.delta:{delta_index}:{digest}",
                    )
                    delta_index += 1
            elif event.event == "citations":
                citations = _safe_records(
                    event.data.get("citations"),
                    _CITATION_FIELDS,
                )
            elif event.event == "done":
                try:
                    message_id = UUID(str(event.data["message_id"]))
                    prompt_tokens = _integer(event.data.get("prompt_tokens"))
                    completion_tokens = _integer(event.data.get("completion_tokens"))
                except (KeyError, ValueError):
                    await self._persist(
                        self._lifecycle.fail(identity.run_id, error_code="internal")
                    )
                    return "failed"
                no_answer = event.data.get("no_answer") is True
                await self._append(
                    identity,
                    "message.completed",
                    {
                        "message_id": str(message_id),
                        "no_answer": no_answer,
                        "citations": citations,
                    },
                    dedupe_key="message.completed",
                )
                await self._append(
                    identity,
                    "usage.updated",
                    {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                    },
                    dedupe_key="usage.updated",
                )
                completed = await self._persist(
                    self._lifecycle.complete(
                        identity.run_id,
                        assistant_message_id=message_id,
                        usage=(prompt_tokens, completion_tokens),
                    )
                )
                return "completed" if completed else "cancelled"
            elif event.event == "error":
                await self._persist(
                    self._lifecycle.fail(
                        identity.run_id,
                        error_code="provider_rejected",
                    )
                )
                return "failed"

        await self._persist(self._lifecycle.fail(identity.run_id, error_code="internal"))
        return "failed"
