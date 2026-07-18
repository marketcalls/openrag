import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.errors import ConflictError, NotFoundError
from openrag.modules.auth.models import User
from openrag.modules.chat.models import Chat, Message
from openrag.modules.chat.service import (
    active_leaf,
    add_message,
    create_chat,
    delete_chat,
    get_chat,
    list_chats,
    list_messages,
    rename_chat,
    resolve_parent,
)
from openrag.modules.tenancy.authorization import AuthorizationSnapshot
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import Workspace, WorkspaceMember


async def make_ctx(
    session: AsyncSession,
    user: User,
) -> tuple[TenantContext, Workspace]:
    workspace = Workspace(org_id=user.org_id, name="Workspace")
    session.add(workspace)
    await session.flush()
    session.add(
        WorkspaceMember(
            org_id=user.org_id,
            workspace_id=workspace.id,
            user_id=user.id,
        )
    )
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
    return context, workspace


async def build_turn(
    session: AsyncSession,
    context: TenantContext,
    chat: Chat,
    question: str,
    answer: str,
    parent: Message | None,
) -> tuple[Message, Message]:
    user_message = await add_message(
        session,
        context,
        chat,
        role="user",
        content=question,
        parent=parent,
    )
    assistant_message = await add_message(
        session,
        context,
        chat,
        role="assistant",
        content=answer,
        parent=user_message,
    )
    return user_message, assistant_message


async def test_crud_and_scoping(
    session: AsyncSession,
    seeded_user: User,
) -> None:
    context, workspace = await make_ctx(session, seeded_user)
    chat = await create_chat(
        session,
        context,
        workspace_id=workspace.id,
    )
    assert [item.id for item in await list_chats(session, context)] == [
        chat.id
    ]

    renamed = await rename_chat(
        session,
        context,
        chat.id,
        "Q3 numbers",
    )
    assert renamed.title == "Q3 numbers"

    other = TenantContext(
        user_id=workspace.id,
        org_id=context.org_id,
        authorization=AuthorizationSnapshot(
            user_id=workspace.id,
            org_id=context.org_id,
            is_platform_superadmin=False,
            org_permissions=frozenset({"chat.use"}),
            workspace_permissions={},
            workspace_ids=frozenset(),
        ),
    )
    with pytest.raises(NotFoundError):
        await get_chat(session, other, chat.id)
    with pytest.raises(NotFoundError):
        await add_message(
            session,
            other,
            chat,
            role="user",
            content="not mine",
            parent=None,
        )

    await delete_chat(session, context, chat.id)
    assert await list_chats(session, context) == []


async def test_alternation_and_dense_siblings(
    session: AsyncSession,
    seeded_user: User,
) -> None:
    context, workspace = await make_ctx(session, seeded_user)
    chat = await create_chat(
        session,
        context,
        workspace_id=workspace.id,
    )
    first_user, _ = await build_turn(
        session,
        context,
        chat,
        "question 1",
        "answer 1",
        parent=None,
    )

    with pytest.raises(ConflictError):
        await add_message(
            session,
            context,
            chat,
            role="user",
            content="user under user",
            parent=first_user,
        )
    with pytest.raises(ConflictError):
        await add_message(
            session,
            context,
            chat,
            role="assistant",
            content="assistant root",
            parent=None,
        )

    edited_root = await add_message(
        session,
        context,
        chat,
        role="user",
        content="question 1 revised",
        parent=None,
    )
    assert (edited_root.sibling_index, first_user.sibling_index) == (1, 0)

    regenerated = await add_message(
        session,
        context,
        chat,
        role="assistant",
        content="answer 1 revised",
        parent=first_user,
    )
    assert regenerated.sibling_index == 1


async def test_active_leaf_and_parent_resolution(
    session: AsyncSession,
    seeded_user: User,
) -> None:
    context, workspace = await make_ctx(session, seeded_user)
    chat = await create_chat(
        session,
        context,
        workspace_id=workspace.id,
    )
    _, first_answer = await build_turn(
        session,
        context,
        chat,
        "question 1",
        "answer 1",
        parent=None,
    )
    second_user, _ = await build_turn(
        session,
        context,
        chat,
        "question 2",
        "answer 2",
        parent=first_answer,
    )
    _, edited_answer = await build_turn(
        session,
        context,
        chat,
        "question 2 revised",
        "answer 2 revised",
        parent=first_answer,
    )
    messages = await list_messages(session, chat.id)

    leaf = active_leaf(messages)
    assert leaf is not None
    assert leaf.id == edited_answer.id
    assert resolve_parent(messages, None, explicit=False).id == leaf.id
    assert resolve_parent(messages, first_answer.id, explicit=True).id == (
        first_answer.id
    )
    assert resolve_parent(messages, None, explicit=True) is None

    dangling = await add_message(
        session,
        context,
        chat,
        role="user",
        content="interrupted question",
        parent=edited_answer,
    )
    messages = await list_messages(session, chat.id)
    assert active_leaf(messages).id == dangling.id
    assert resolve_parent(messages, None, explicit=False).id == (
        edited_answer.id
    )

    with pytest.raises(NotFoundError):
        resolve_parent(messages, second_user.chat_id, explicit=True)


async def test_membership_required_for_create(
    session: AsyncSession,
    seeded_user: User,
) -> None:
    _, workspace = await make_ctx(session, seeded_user)
    stranger = TenantContext(
        user_id=seeded_user.id,
        org_id=seeded_user.org_id,
        authorization=AuthorizationSnapshot(
            user_id=seeded_user.id,
            org_id=seeded_user.org_id,
            is_platform_superadmin=False,
            org_permissions=frozenset({"chat.use"}),
            workspace_permissions={},
            workspace_ids=frozenset(),
        ),
    )

    with pytest.raises(NotFoundError):
        await create_chat(
            session,
            stranger,
            workspace_id=workspace.id,
        )
