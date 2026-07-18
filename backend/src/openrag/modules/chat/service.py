"""Tenant-scoped conversation tree CRUD and structural invariants."""

from collections import defaultdict
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.db import naive_utc
from openrag.core.errors import ConflictError, NotFoundError
from openrag.modules.chat.models import Chat, Citation, Message
from openrag.modules.tenancy import service as tenancy_service
from openrag.modules.tenancy.context import TenantContext

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
_ROLES = {ROLE_USER, ROLE_ASSISTANT}


async def create_chat(
    session: AsyncSession,
    context: TenantContext,
    *,
    workspace_id: UUID,
    title: str | None = None,
) -> Chat:
    await tenancy_service.get_workspace(session, context, workspace_id)
    chat = Chat(
        org_id=context.org_id,
        workspace_id=workspace_id,
        user_id=context.user_id,
    )
    if title:
        chat.title = title
    session.add(chat)
    await session.commit()
    return chat


async def list_chats(
    session: AsyncSession,
    context: TenantContext,
) -> list[Chat]:
    statement = (
        select(Chat)
        .where(
            Chat.org_id == context.org_id,
            Chat.user_id == context.user_id,
        )
        .order_by(Chat.updated_at.desc())
    )
    return list((await session.execute(statement)).scalars())


async def get_chat(
    session: AsyncSession,
    context: TenantContext,
    chat_id: UUID,
) -> Chat:
    chat = (
        await session.execute(
            select(Chat).where(
                Chat.id == chat_id,
                Chat.org_id == context.org_id,
                Chat.user_id == context.user_id,
            )
        )
    ).scalar_one_or_none()
    if chat is None:
        raise NotFoundError("chat not found")
    return chat


async def rename_chat(
    session: AsyncSession,
    context: TenantContext,
    chat_id: UUID,
    title: str,
) -> Chat:
    chat = await get_chat(session, context, chat_id)
    chat.title = title
    await session.commit()
    return chat


async def delete_chat(
    session: AsyncSession,
    context: TenantContext,
    chat_id: UUID,
) -> None:
    chat = await get_chat(session, context, chat_id)
    await session.delete(chat)
    await session.commit()


async def list_messages(
    session: AsyncSession,
    chat_id: UUID,
) -> list[Message]:
    statement = (
        select(Message)
        .where(Message.chat_id == chat_id)
        .order_by(Message.created_at)
    )
    return list((await session.execute(statement)).scalars())


async def get_message(
    session: AsyncSession,
    context: TenantContext,
    message_id: UUID,
) -> tuple[Chat, Message]:
    message = (
        await session.execute(
            select(Message).where(Message.id == message_id)
        )
    ).scalar_one_or_none()
    if message is None:
        raise NotFoundError("message not found")
    chat = await get_chat(session, context, message.chat_id)
    return chat, message


async def list_citations(
    session: AsyncSession,
    chat_id: UUID,
) -> dict[UUID, list[Citation]]:
    statement = (
        select(Citation)
        .join(Message, Message.id == Citation.message_id)
        .where(Message.chat_id == chat_id)
        .order_by(Citation.marker)
    )
    by_message: dict[UUID, list[Citation]] = defaultdict(list)
    for citation in (await session.execute(statement)).scalars():
        by_message[citation.message_id].append(citation)
    return by_message


def active_leaf(messages: list[Message]) -> Message | None:
    """Follow the highest sibling index at each level of the tree."""
    children: dict[UUID | None, list[Message]] = defaultdict(list)
    for message in messages:
        children[message.parent_message_id].append(message)

    node: Message | None = None
    branch = children.get(None, [])
    while branch:
        node = max(branch, key=lambda item: item.sibling_index)
        branch = children.get(node.id, [])
    return node


def resolve_parent(
    messages: list[Message],
    parent_message_id: UUID | None,
    explicit: bool,
) -> Message | None:
    """Resolve append, edit-and-resend, and interrupted-stream semantics."""
    if explicit:
        if parent_message_id is None:
            return None
        by_id = {message.id: message for message in messages}
        parent = by_id.get(parent_message_id)
        if parent is None:
            raise NotFoundError("parent message not found in this chat")
        return parent

    leaf = active_leaf(messages)
    if leaf is not None and leaf.role == ROLE_USER:
        by_id = {message.id: message for message in messages}
        if leaf.parent_message_id is None:
            return None
        return by_id.get(leaf.parent_message_id)
    return leaf


async def add_message(
    session: AsyncSession,
    context: TenantContext,
    chat: Chat,
    *,
    role: str,
    content: str,
    parent: Message | None,
    model_id: UUID | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> Message:
    if chat.org_id != context.org_id or chat.user_id != context.user_id:
        raise NotFoundError("chat not found")
    if role not in _ROLES:
        raise ConflictError("invalid message role")
    if parent is None:
        if role != ROLE_USER:
            raise ConflictError("root messages must be user messages")
    elif parent.chat_id != chat.id:
        raise NotFoundError("parent message not found in this chat")
    elif parent.role == role:
        raise ConflictError("message roles must alternate")

    sibling_count = (
        await session.execute(
            select(func.count())
            .select_from(Message)
            .where(
                Message.chat_id == chat.id,
                Message.parent_message_id
                == (parent.id if parent is not None else None),
            )
        )
    ).scalar_one()
    message = Message(
        chat_id=chat.id,
        parent_message_id=parent.id if parent is not None else None,
        sibling_index=sibling_count,
        role=role,
        content=content,
        model_id=model_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    session.add(message)
    chat.updated_at = naive_utc()
    await session.commit()
    return message
