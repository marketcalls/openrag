"""Persist bounded context accounting without retaining raw prompts."""

import hashlib
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.modules.chat.prompting import PromptContextSnapshot, estimate_tokens
from openrag.modules.memory.models import MemoryRecord
from openrag.modules.runs.lifecycle import RunIdentity
from openrag.modules.runs.models import RunContextLedger, RunMemorySelection


def selection_digest(memories: Sequence[MemoryRecord]) -> str:
    payload = "\x1e".join(
        f"{memory.id}:{memory.content_hash}" for memory in memories
    ).encode()
    return hashlib.sha256(payload).hexdigest()


async def record_run_context(
    session_factory: async_sessionmaker[AsyncSession],
    identity: RunIdentity,
    *,
    attempt: int,
    snapshot: PromptContextSnapshot,
    memories: Sequence[MemoryRecord],
) -> None:
    if len(memories) != snapshot.memory_items:
        raise ValueError("context memory count does not match snapshot")
    if any(
        memory.org_id != identity.org_id
        or memory.workspace_id != identity.workspace_id
        for memory in memories
    ):
        raise ValueError("context memory crosses the run tenant boundary")
    digest = selection_digest(memories)
    async with session_factory.begin() as session:
        existing = await session.scalar(
            select(RunContextLedger).where(
                RunContextLedger.org_id == identity.org_id,
                RunContextLedger.run_id == identity.run_id,
                RunContextLedger.attempt == attempt,
            )
        )
        if existing is not None:
            if existing.selection_digest != digest:
                raise RuntimeError("run_context_attempt_changed")
            return
        ledger = RunContextLedger(
            org_id=identity.org_id,
            workspace_id=identity.workspace_id,
            run_id=identity.run_id,
            attempt=attempt,
            route=snapshot.route,
            budget_tokens=snapshot.budget_tokens,
            estimated_prompt_tokens=snapshot.estimated_prompt_tokens,
            memory_tokens=snapshot.memory_tokens,
            memory_items=snapshot.memory_items,
            history_tokens=snapshot.history_tokens,
            history_messages=snapshot.history_messages,
            retrieval_tokens=snapshot.retrieval_tokens,
            retrieval_items=snapshot.retrieval_items,
            selection_digest=digest,
        )
        session.add(ledger)
        await session.flush()
        session.add_all(
            RunMemorySelection(
                org_id=identity.org_id,
                workspace_id=identity.workspace_id,
                ledger_id=ledger.id,
                memory_id=memory.id,
                rank=rank,
                estimated_tokens=max(
                    1,
                    estimate_tokens(f"{memory.canonical_key}: {memory.content}"),
                ),
                content_hash=memory.content_hash,
            )
            for rank, memory in enumerate(memories, start=1)
        )
