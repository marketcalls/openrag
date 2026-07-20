"""Bounded, deterministic selection of active user-approved memories."""

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.memory.models import MemoryPreference, MemoryRecord
from openrag.modules.tenancy.context import TenantContext

DEFAULT_MAX_ITEMS = 8
DEFAULT_MAX_TOKENS = 400
_WORD_RE = re.compile(r"[\w-]{2,}", re.UNICODE)
_STYLE_PREFIXES = ("answer.", "response.", "preference.", "ui.")


def _terms(value: str) -> set[str]:
    return {term.casefold() for term in _WORD_RE.findall(value)}


def _estimated_tokens(memory: MemoryRecord) -> int:
    return max(1, (len(memory.canonical_key) + len(memory.content) + 4) // 4)


def rank_memory_candidates(
    candidates: Sequence[MemoryRecord],
    *,
    query: str,
    semantic_enabled: bool,
    episodic_enabled: bool,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> tuple[MemoryRecord, ...]:
    if not 1 <= max_items <= DEFAULT_MAX_ITEMS:
        raise ValueError("memory item budget must be between 1 and 8")
    if not 1 <= max_tokens <= DEFAULT_MAX_TOKENS:
        raise ValueError("memory token budget must be between 1 and 400")
    query_terms = _terms(query)
    ranked: list[tuple[float, datetime, MemoryRecord]] = []
    for memory in candidates:
        if memory.memory_type == "semantic" and not semantic_enabled:
            continue
        if memory.memory_type == "episodic" and not episodic_enabled:
            continue
        if memory.memory_type not in {"semantic", "episodic"}:
            continue
        memory_terms = _terms(f"{memory.canonical_key} {memory.content}")
        overlap = len(query_terms & memory_terms) / max(1, len(query_terms))
        is_style = memory.canonical_key.startswith(_STYLE_PREFIXES)
        if memory.memory_type == "episodic" and overlap == 0:
            continue
        score = (
            memory.importance * 0.4
            + memory.confidence * 0.1
            + overlap * 0.5
            + (0.05 if is_style else 0)
        )
        ranked.append((score, memory.updated_at, memory))
    ranked.sort(key=lambda item: (item[0], item[1], item[2].id), reverse=True)

    selected: list[MemoryRecord] = []
    seen_keys: set[str] = set()
    remaining = max_tokens
    for _score, _updated_at, memory in ranked:
        if memory.canonical_key in seen_keys:
            continue
        cost = _estimated_tokens(memory)
        if cost > remaining:
            continue
        selected.append(memory)
        seen_keys.add(memory.canonical_key)
        remaining -= cost
        if len(selected) == max_items:
            break
    return tuple(selected)


async def select_memories(
    session: AsyncSession,
    context: TenantContext,
    workspace_id: UUID,
    *,
    query: str,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> tuple[MemoryRecord, ...]:
    now = datetime.now(UTC).replace(tzinfo=None)
    preference = await session.scalar(
        select(MemoryPreference).where(
            MemoryPreference.org_id == context.org_id,
            MemoryPreference.workspace_id == workspace_id,
            MemoryPreference.user_id == context.user_id,
        )
    )
    semantic_enabled = preference.semantic_enabled if preference is not None else True
    episodic_enabled = preference.episodic_enabled if preference is not None else False
    candidates = list(
        (
            await session.execute(
                select(MemoryRecord)
                .where(
                    MemoryRecord.org_id == context.org_id,
                    MemoryRecord.workspace_id == workspace_id,
                    MemoryRecord.user_id == context.user_id,
                    MemoryRecord.scope == "user_workspace",
                    MemoryRecord.status == "active",
                    MemoryRecord.source_trust == "explicit_user",
                    or_(
                        MemoryRecord.expires_at.is_(None),
                        MemoryRecord.expires_at > now,
                    ),
                )
                .order_by(
                    MemoryRecord.importance.desc(),
                    MemoryRecord.updated_at.desc(),
                    MemoryRecord.id.desc(),
                )
                .limit(64)
            )
        ).scalars()
    )
    return rank_memory_candidates(
        candidates,
        query=query,
        semantic_enabled=semantic_enabled,
        episodic_enabled=episodic_enabled,
        max_items=max_items,
        max_tokens=max_tokens,
    )
