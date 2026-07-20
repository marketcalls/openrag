from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.chat.models import Chat, Message
from openrag.modules.events.models import OutboxEvent
from openrag.modules.runs.schemas import RunCreate, RunRegenerate
from openrag.modules.runs.service import (
    accept_regeneration,
    accept_run,
    request_cancel,
)
from openrag.modules.tenancy.authorization import AuthorizationSnapshot
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import Workspace, WorkspaceMember


async def _run_context(
    session: AsyncSession,
    user: User,
) -> tuple[TenantContext, Chat]:
    workspace = Workspace(org_id=user.org_id, name="Durable Runs")
    session.add(workspace)
    await session.flush()
    session.add(
        WorkspaceMember(
            org_id=user.org_id,
            workspace_id=workspace.id,
            user_id=user.id,
        )
    )
    chat = Chat(
        org_id=user.org_id,
        workspace_id=workspace.id,
        user_id=user.id,
    )
    session.add(chat)
    await session.commit()
    context = TenantContext(
        user_id=user.id,
        org_id=user.org_id,
        authorization=AuthorizationSnapshot(
            user_id=user.id,
            org_id=user.org_id,
            is_platform_superadmin=False,
            org_permissions=frozenset({"chat.use"}),
            workspace_permissions={},
            workspace_ids=frozenset({workspace.id}),
        ),
    )
    return context, chat


async def test_accept_run_persists_message_run_and_outbox_atomically(
    session: AsyncSession,
    seeded_user: User,
) -> None:
    context, chat = await _run_context(session, seeded_user)
    request_id = uuid4()

    accepted = await accept_run(
        session,
        context,
        chat.id,
        RunCreate(content="hello", client_request_id=request_id),
    )

    assert accepted.created is True
    assert accepted.run.client_request_id == request_id
    assert accepted.run.status == "accepted"
    assert chat.title == "hello"
    assert await session.scalar(select(func.count()).select_from(Message)) == 1
    assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
    outbox = await session.scalar(select(OutboxEvent))
    assert outbox is not None
    assert outbox.event_type == "run.requested.v1"
    assert "content" not in repr(outbox.payload)


async def test_accept_run_replay_does_not_duplicate_effects(
    session: AsyncSession,
    seeded_user: User,
) -> None:
    context, chat = await _run_context(session, seeded_user)
    command = RunCreate(content="hello", client_request_id=uuid4())

    first = await accept_run(session, context, chat.id, command)
    second = await accept_run(session, context, chat.id, command)

    assert first.run.id == second.run.id
    assert first.created is True
    assert second.created is False
    assert await session.scalar(select(func.count()).select_from(Message)) == 1
    assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 1


async def test_cancel_is_idempotent_and_emits_one_command(
    session: AsyncSession,
    seeded_user: User,
) -> None:
    context, chat = await _run_context(session, seeded_user)
    accepted = await accept_run(
        session,
        context,
        chat.id,
        RunCreate(content="hello", client_request_id=uuid4()),
    )

    first = await request_cancel(session, context, accepted.run.id)
    second = await request_cancel(session, context, accepted.run.id)

    assert first.cancel_requested_at is not None
    assert second.cancel_requested_at == first.cancel_requested_at
    assert (
        await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.event_type == "run.cancel.requested.v1")
        )
        == 1
    )


async def test_regeneration_reuses_user_turn_without_duplicating_message(
    session: AsyncSession,
    seeded_user: User,
) -> None:
    context, chat = await _run_context(session, seeded_user)
    original = await accept_run(
        session,
        context,
        chat.id,
        RunCreate(content="hello", client_request_id=uuid4()),
    )
    assistant = Message(
        org_id=context.org_id,
        workspace_id=chat.workspace_id,
        chat_id=chat.id,
        parent_message_id=original.run.input_message_id,
        sibling_index=0,
        role="assistant",
        content="first answer",
    )
    session.add(assistant)
    await session.commit()

    regenerated = await accept_regeneration(
        session,
        context,
        assistant.id,
        RunRegenerate(client_request_id=uuid4()),
    )

    assert regenerated.run.id != original.run.id
    assert regenerated.run.input_message_id == original.run.input_message_id
    assert await session.scalar(select(func.count()).select_from(Message)) == 2
    assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 2
