"""Tenant-scoped conversation trees and retrieval-backed reply streaming."""

from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import Settings
from openrag.core.db import naive_utc
from openrag.core.errors import ConflictError, NotFoundError, UpstreamError
from openrag.modules.chat.events import (
    CitationRef,
    SourceRef,
    SSEEvent,
    citations_event,
    done_event,
    error_event,
    retrieval_started_event,
    sources_event,
    token_event,
)
from openrag.modules.chat.llm import LLMDelta, LLMStreamer, LLMUsage
from openrag.modules.chat.models import Chat, Citation, Message
from openrag.modules.chat.prompting import (
    PromptSource,
    build_messages,
    parse_citation_markers,
)
from openrag.modules.chat.schemas import ChatTreeOut, CitationOut, MessageNode
from openrag.modules.documents import service as documents_service
from openrag.modules.models.models import Model
from openrag.modules.retrieval.service import RetrievalResult
from openrag.modules.tenancy import service as tenancy_service
from openrag.modules.tenancy.context import TenantContext

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
_ROLES = {ROLE_USER, ROLE_ASSISTANT}
NO_ANSWER_TEXT = (
    "I couldn't find anything in this workspace's documents that answers "
    "that. The closest sources are shown, but none scored above the "
    "workspace's confidence threshold. Try rephrasing, or check that the "
    "relevant documents are uploaded and indexed."
)
_SNIPPET_CHARS = 300


class Retriever(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        context: TenantContext,
        workspace_id: UUID,
        query: str,
        top_k: int = 8,
    ) -> RetrievalResult: ...


async def create_chat(
    session: AsyncSession,
    context: TenantContext,
    *,
    workspace_id: UUID,
    title: str | None = None,
) -> Chat:
    await tenancy_service.get_workspace(
        session,
        context,
        workspace_id,
        "chat.use",
    )
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


def path_to_root(messages: list[Message], leaf: Message) -> list[Message]:
    """Return a leaf's ancestors in oldest-to-newest order."""
    by_id = {message.id: message for message in messages}
    path: list[Message] = []
    parent_id = leaf.parent_message_id
    while parent_id is not None:
        node = by_id[parent_id]
        path.append(node)
        parent_id = node.parent_message_id
    path.reverse()
    return path


async def _source_refs(
    session: AsyncSession,
    context: TenantContext,
    result: RetrievalResult,
) -> list[SourceRef]:
    filenames: dict[UUID, str] = {}
    references: list[SourceRef] = []
    for marker, chunk in enumerate(result.chunks, start=1):
        if chunk.document_id not in filenames:
            document = await documents_service.get_document_checked(
                session,
                context,
                chunk.document_id,
            )
            filenames[chunk.document_id] = document.filename
        references.append(
            SourceRef(
                marker=marker,
                document_id=str(chunk.document_id),
                filename=filenames[chunk.document_id],
                page=chunk.page,
                chunk_index=chunk.chunk_index,
                score=chunk.score,
                snippet=chunk.text[:_SNIPPET_CHARS],
            )
        )
    return references


async def _persist_assistant(
    session: AsyncSession,
    context: TenantContext,
    chat: Chat,
    *,
    parent: Message,
    content: str,
    model_id: UUID | None,
    usage: LLMUsage | None,
    citations: list[CitationRef],
) -> Message:
    message = await add_message(
        session,
        context,
        chat,
        role=ROLE_ASSISTANT,
        content=content,
        parent=parent,
        model_id=model_id,
        prompt_tokens=usage.prompt_tokens if usage is not None else None,
        completion_tokens=(
            usage.completion_tokens if usage is not None else None
        ),
    )
    for citation in citations:
        session.add(
            Citation(
                message_id=message.id,
                document_id=UUID(citation.document_id),
                chunk_ref=citation.chunk_ref,
                page=citation.page,
                score=citation.score,
                marker=citation.marker,
            )
        )
    await session.commit()
    return message


async def stream_reply(
    session: AsyncSession,
    context: TenantContext,
    *,
    chat: Chat,
    user_message: Message,
    model: Model,
    streamer: LLMStreamer,
    retriever: Retriever,
    settings: Settings,
) -> AsyncIterator[SSEEvent]:
    yield retrieval_started_event()
    result = await retriever(
        session,
        context,
        chat.workspace_id,
        user_message.content,
    )
    sources = await _source_refs(session, context, result)
    yield sources_event(sources)

    if result.no_answer:
        yield token_event(NO_ANSWER_TEXT)
        message = await _persist_assistant(
            session,
            context,
            chat,
            parent=user_message,
            content=NO_ANSWER_TEXT,
            model_id=None,
            usage=None,
            citations=[],
        )
        yield citations_event([])
        yield done_event(
            message_id=str(message.id),
            prompt_tokens=0,
            completion_tokens=0,
            no_answer=True,
        )
        return

    all_messages = await list_messages(session, chat.id)
    history = [
        (message.role, message.content)
        for message in path_to_root(all_messages, user_message)
    ]
    prompt = build_messages(
        sources=[
            PromptSource(
                marker=source.marker,
                filename=source.filename,
                page=source.page,
                text=result.chunks[source.marker - 1].text,
            )
            for source in sources
        ],
        history=history,
        user_query=user_message.content,
        budget=settings.chat_context_token_budget,
    )

    parts: list[str] = []
    usage: LLMUsage | None = None
    try:
        async for item in streamer.stream(
            model=model.litellm_model_name,
            messages=prompt,
        ):
            if isinstance(item, LLMDelta):
                parts.append(item.text)
                yield token_event(item.text)
            else:
                usage = item
    except UpstreamError as exc:
        yield error_event(exc.detail or "LLM gateway error")
        return

    answer = "".join(parts)
    markers = parse_citation_markers(answer, len(sources))
    by_marker = {source.marker: source for source in sources}
    citation_references = [
        CitationRef(
            marker=marker,
            document_id=by_marker[marker].document_id,
            chunk_ref=(
                f"{by_marker[marker].document_id}:"
                f"{by_marker[marker].page}:"
                f"{by_marker[marker].chunk_index}"
            ),
            page=by_marker[marker].page,
            score=by_marker[marker].score,
        )
        for marker in markers
    ]
    message = await _persist_assistant(
        session,
        context,
        chat,
        parent=user_message,
        content=answer,
        model_id=model.id,
        usage=usage,
        citations=citation_references,
    )
    yield citations_event(citation_references)
    yield done_event(
        message_id=str(message.id),
        prompt_tokens=usage.prompt_tokens if usage is not None else 0,
        completion_tokens=(
            usage.completion_tokens if usage is not None else 0
        ),
        no_answer=False,
    )


def build_tree(
    messages: list[Message],
    citations: dict[UUID, list[Citation]],
) -> list[MessageNode]:
    children: dict[UUID | None, list[Message]] = defaultdict(list)
    for message in messages:
        children[message.parent_message_id].append(message)

    def node(message: Message) -> MessageNode:
        child_messages = sorted(
            children.get(message.id, []),
            key=lambda child: child.sibling_index,
        )
        return MessageNode(
            id=message.id,
            parent_message_id=message.parent_message_id,
            sibling_index=message.sibling_index,
            role=message.role,
            content=message.content,
            model_id=message.model_id,
            prompt_tokens=message.prompt_tokens,
            completion_tokens=message.completion_tokens,
            created_at=message.created_at,
            citations=[
                CitationOut.model_validate(citation)
                for citation in citations.get(message.id, [])
            ],
            children=[node(child) for child in child_messages],
        )

    roots = sorted(
        children.get(None, []),
        key=lambda message: message.sibling_index,
    )
    return [node(root) for root in roots]


async def get_chat_tree(
    session: AsyncSession,
    context: TenantContext,
    chat_id: UUID,
) -> ChatTreeOut:
    chat = await get_chat(session, context, chat_id)
    messages = await list_messages(session, chat_id)
    citations = await list_citations(session, chat_id)
    return ChatTreeOut(
        id=chat.id,
        workspace_id=chat.workspace_id,
        title=chat.title,
        messages=build_tree(messages, citations),
    )
