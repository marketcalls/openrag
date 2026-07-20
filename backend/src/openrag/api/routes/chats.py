from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.api.deps import get_session
from openrag.core.config import Settings, get_settings
from openrag.core.errors import ConflictError
from openrag.modules.chat import service
from openrag.modules.chat.events import SSEEvent
from openrag.modules.chat.models import Chat
from openrag.modules.chat.schemas import (
    ChatCreate,
    ChatOut,
    ChatPageOut,
    ChatPatch,
    ChatTreeOut,
    MessageSend,
    RegenerateRequest,
)
from openrag.modules.models import service as models_service
from openrag.modules.models.models import Model
from openrag.modules.orchestration.runtime import (
    ModelExecution,
    create_model_execution,
)
from openrag.modules.tenancy import service as tenancy_service
from openrag.modules.tenancy.context import (
    TenantContext,
    get_tenant_context,
    rate_limit_user,
)
from openrag.modules.tenancy.models import Workspace

router = APIRouter(tags=["chat"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
ContextDep = Annotated[TenantContext, Depends(get_tenant_context)]
SendContextDep = Annotated[
    TenantContext,
    Depends(rate_limit_user("chat_send", 30, 60)),
]

_SSE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",
}


async def _execution(
    request: Request,
    settings: Settings,
    session: AsyncSession,
    model: Model,
    context: TenantContext,
    workspace: Workspace,
) -> ModelExecution:
    injected = request.app.state.llm_streamer
    if injected is not None:
        return ModelExecution(
            streamer=injected,
            agent_gatherer_factory=None,
            answer_validator=None,
            analytics_composer=None,
        )
    return await create_model_execution(
        session,
        model,
        settings,
        session_factory=request.app.state.session_factory,
        context=context,
        workspace_id=workspace.id,
        document_authority_enabled=workspace.document_authority_enabled,
    )


async def _encoded(
    events: AsyncIterator[SSEEvent],
) -> AsyncIterator[str]:
    async for event in events:
        yield event.encode()


def _sse(events: AsyncIterator[SSEEvent]) -> StreamingResponse:
    return StreamingResponse(
        _encoded(events),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


async def _resolve_model(
    session: AsyncSession,
    context: TenantContext,
    chat: Chat,
    requested_model_id: UUID | None,
) -> tuple[Workspace, Model]:
    workspace = await tenancy_service.get_workspace(
        session,
        context,
        chat.workspace_id,
        "chat.use",
    )
    return (
        workspace,
        await models_service.resolve_model(
            session,
            requested_model_id=requested_model_id,
            default_model_id=workspace.default_model_id,
        ),
    )


@router.post("/chats", status_code=201, response_model=ChatOut)
async def create_chat(
    body: ChatCreate,
    session: SessionDep,
    context: ContextDep,
) -> ChatOut:
    chat = await service.create_chat(
        session,
        context,
        workspace_id=body.workspace_id,
        title=body.title,
    )
    return ChatOut.model_validate(chat)


@router.post("/chats/{chat_id}/messages")
async def send_message(
    chat_id: UUID,
    body: MessageSend,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    context: SendContextDep,
) -> StreamingResponse:
    chat = await service.get_chat(session, context, chat_id)
    workspace, model = await _resolve_model(
        session,
        context,
        chat,
        body.model_id,
    )
    messages = await service.list_messages(session, chat.id)
    parent = service.resolve_parent(
        messages,
        body.parent_message_id,
        explicit="parent_message_id" in body.model_fields_set,
    )
    execution = await _execution(
        request,
        settings,
        session,
        model,
        context,
        workspace,
    )
    user_message = await service.add_message(
        session,
        context,
        chat,
        role=service.ROLE_USER,
        content=body.content,
        parent=parent,
    )
    return _sse(
        service.stream_reply(
            session,
            context,
            chat=chat,
            user_message=user_message,
            model=model,
            streamer=execution.streamer,
            retriever=request.app.state.retriever,
            citation_backfiller=request.app.state.citation_backfiller,
            settings=settings,
            agent_gatherer_factory=execution.agent_gatherer_factory,
            answer_validator=execution.answer_validator,
            analytics_composer=execution.analytics_composer,
            retrieval_min_score=workspace.min_score,
        )
    )


@router.post("/messages/{message_id}/regenerate")
async def regenerate(
    message_id: UUID,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    context: SendContextDep,
    body: RegenerateRequest | None = None,
) -> StreamingResponse:
    chat, message = await service.get_message(
        session,
        context,
        message_id,
    )
    if message.role != service.ROLE_ASSISTANT or message.parent_message_id is None:
        raise ConflictError("only assistant messages can be regenerated")
    workspace, model = await _resolve_model(
        session,
        context,
        chat,
        body.model_id if body is not None else None,
    )
    messages = await service.list_messages(session, chat.id)
    user_message = next(item for item in messages if item.id == message.parent_message_id)
    execution = await _execution(
        request,
        settings,
        session,
        model,
        context,
        workspace,
    )
    return _sse(
        service.stream_reply(
            session,
            context,
            chat=chat,
            user_message=user_message,
            model=model,
            streamer=execution.streamer,
            retriever=request.app.state.retriever,
            citation_backfiller=request.app.state.citation_backfiller,
            settings=settings,
            agent_gatherer_factory=execution.agent_gatherer_factory,
            answer_validator=execution.answer_validator,
            analytics_composer=execution.analytics_composer,
            retrieval_min_score=workspace.min_score,
        )
    )


@router.get("/chats", response_model=list[ChatOut])
async def list_chats(
    session: SessionDep,
    context: ContextDep,
) -> list[ChatOut]:
    return [ChatOut.model_validate(chat) for chat in await service.list_chats(session, context)]


@router.get("/chats/search", response_model=ChatPageOut)
async def search_chats(
    session: SessionDep,
    context: ContextDep,
    workspace_id: Annotated[UUID, Query()],
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> ChatPageOut:
    try:
        page = await service.search_chats(
            session,
            context,
            workspace_id=workspace_id,
            query=q,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise ConflictError("chat search cursor is invalid") from exc
    return ChatPageOut(
        items=[ChatOut.model_validate(chat) for chat in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/chats/{chat_id}", response_model=ChatTreeOut)
async def get_chat_tree(
    chat_id: UUID,
    session: SessionDep,
    context: ContextDep,
) -> ChatTreeOut:
    return await service.get_chat_tree(session, context, chat_id)


@router.patch("/chats/{chat_id}", response_model=ChatOut)
async def rename_chat(
    chat_id: UUID,
    body: ChatPatch,
    session: SessionDep,
    context: ContextDep,
) -> ChatOut:
    chat = await service.rename_chat(
        session,
        context,
        chat_id,
        body.title,
    )
    return ChatOut.model_validate(chat)


@router.delete("/chats/{chat_id}", status_code=204)
async def delete_chat(
    chat_id: UUID,
    session: SessionDep,
    context: ContextDep,
) -> None:
    await service.delete_chat(session, context, chat_id)
