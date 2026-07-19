import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.chat.models import Chat, Citation, Message
from openrag.modules.documents.models import Document
from openrag.modules.tenancy.models import Workspace


async def test_tree_rows_and_sibling_constraint(
    session: AsyncSession,
    seeded_user: User,
) -> None:
    workspace = Workspace(org_id=seeded_user.org_id, name="Workspace")
    session.add(workspace)
    await session.flush()
    chat = Chat(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        user_id=seeded_user.id,
    )
    session.add(chat)
    await session.flush()
    root = Message(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        chat_id=chat.id,
        parent_message_id=None,
        sibling_index=0,
        role="user",
        content="question",
    )
    session.add(root)
    await session.flush()
    answer = Message(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        chat_id=chat.id,
        parent_message_id=root.id,
        sibling_index=0,
        role="assistant",
        content="answer [1]",
    )
    session.add(answer)
    await session.flush()
    document = Document(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        filename="source.pdf",
        mime="application/pdf",
        size_bytes=10,
        content_hash="chat-model-source",
        storage_key="chat-model/source.pdf",
        owner_id=seeded_user.id,
        created_by=seeded_user.id,
    )
    session.add(document)
    await session.flush()
    session.add(
        Citation(
            org_id=seeded_user.org_id,
            workspace_id=workspace.id,
            message_id=answer.id,
            document_id=document.id,
            chunk_ref="document:1:0",
            page=1,
            score=0.9,
            marker=1,
        )
    )
    await session.commit()

    assert chat.title == "New chat"
    assert answer.prompt_tokens is None
    assert answer.completion_tokens is None

    duplicate = Message(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        chat_id=chat.id,
        parent_message_id=root.id,
        sibling_index=0,
        role="assistant",
        content="duplicate",
    )
    session.add(duplicate)
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()
