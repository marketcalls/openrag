from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from openrag.core.db import build_session_factory
from openrag.modules.auth.models import User
from openrag.modules.chat.models import Chat, Message
from openrag.modules.chat.prompting import PromptContextSnapshot
from openrag.modules.memory.models import MemoryRecord
from openrag.modules.runs.context import record_run_context, selection_digest
from openrag.modules.runs.lifecycle import RunIdentity
from openrag.modules.runs.models import AgentRun, RunContextLedger, RunMemorySelection
from openrag.modules.tenancy.models import Workspace


def test_context_selection_digest_is_ordered_and_content_bound() -> None:
    first = MemoryRecord(
        id=uuid4(),
        org_id=uuid4(),
        workspace_id=uuid4(),
        user_id=uuid4(),
        client_request_id=uuid4(),
        canonical_key="response.style",
        content="Prefer concise answers.",
        memory_type="semantic",
        scope="user_workspace",
        status="active",
        confidence=1,
        importance=1,
        sensitivity="internal",
        policy_version="v1",
        source_trust="explicit_user",
        content_hash="a" * 64,
        suppression_fingerprint="b" * 64,
    )
    second = MemoryRecord(
        id=uuid4(),
        org_id=first.org_id,
        workspace_id=first.workspace_id,
        user_id=first.user_id,
        client_request_id=uuid4(),
        canonical_key="answer.format",
        content="Use tables.",
        memory_type="semantic",
        scope="user_workspace",
        status="active",
        confidence=1,
        importance=1,
        sensitivity="internal",
        policy_version="v1",
        source_trust="explicit_user",
        content_hash="c" * 64,
        suppression_fingerprint="d" * 64,
    )

    digest = selection_digest([first, second])
    assert digest != selection_digest([second, first])
    second.content_hash = "e" * 64
    assert digest != selection_digest([first, second])


async def test_run_context_is_idempotent_per_attempt_without_raw_prompts(
    engine: AsyncEngine,
    session: AsyncSession,
    seeded_user: User,
    chat_env: dict[str, object],
) -> None:
    workspace = chat_env["workspace"]
    assert isinstance(workspace, Workspace)
    chat = Chat(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        user_id=seeded_user.id,
    )
    session.add(chat)
    await session.flush()
    message = Message(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        chat_id=chat.id,
        role="user",
        content="hello",
    )
    session.add(message)
    await session.flush()
    run = AgentRun(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        user_id=seeded_user.id,
        chat_id=chat.id,
        input_message_id=message.id,
        client_request_id=uuid4(),
    )
    memory = MemoryRecord(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        user_id=seeded_user.id,
        client_request_id=uuid4(),
        canonical_key="response.style",
        content="Prefer concise answers.",
        memory_type="semantic",
        scope="user_workspace",
        status="active",
        confidence=1,
        importance=1,
        sensitivity="internal",
        policy_version="v1",
        source_trust="explicit_user",
        content_hash="a" * 64,
        suppression_fingerprint="b" * 64,
    )
    session.add_all([run, memory])
    await session.commit()
    identity = RunIdentity(
        run_id=run.id,
        org_id=run.org_id,
        workspace_id=run.workspace_id,
        chat_id=run.chat_id,
    )
    snapshot = PromptContextSnapshot(
        route="direct",
        budget_tokens=8_000,
        estimated_prompt_tokens=80,
        memory_tokens=20,
        memory_items=1,
        history_tokens=10,
        history_messages=1,
        retrieval_tokens=0,
        retrieval_items=0,
    )
    factory = build_session_factory(engine)

    await record_run_context(factory, identity, attempt=1, snapshot=snapshot, memories=[memory])
    await record_run_context(factory, identity, attempt=1, snapshot=snapshot, memories=[memory])

    assert await session.scalar(select(func.count()).select_from(RunContextLedger)) == 1
    assert await session.scalar(select(func.count()).select_from(RunMemorySelection)) == 1
    ledger = (await session.execute(select(RunContextLedger))).scalar_one()
    assert not hasattr(ledger, "prompt")
    memory.content_hash = "c" * 64
    with pytest.raises(RuntimeError, match="run_context_attempt_changed"):
        await record_run_context(
            factory,
            identity,
            attempt=1,
            snapshot=snapshot,
            memories=[memory],
        )
