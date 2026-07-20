from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.errors import ConflictError, WorkspaceAccessDenied
from openrag.modules.auth.models import User
from openrag.modules.memory.models import MemoryPreference, MemorySuppression
from openrag.modules.memory.schemas import MemoryCreate, MemoryPatch, MemoryPreferencePatch
from openrag.modules.memory.service import (
    create_memory,
    forget_memory,
    get_preferences,
    list_memories,
    update_memory,
    update_preferences,
)
from openrag.modules.tenancy.authorization import AuthorizationSnapshot
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import Workspace


def memory_context(user: User, workspace: Workspace) -> TenantContext:
    return TenantContext(
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


async def test_memory_lifecycle_is_idempotent_and_suppressed_after_forget(
    session: AsyncSession,
    seeded_user: User,
    chat_env: dict[str, object],
) -> None:
    workspace = chat_env["workspace"]
    assert isinstance(workspace, Workspace)
    context = memory_context(seeded_user, workspace)
    create_request = uuid4()
    body = MemoryCreate(
        client_request_id=create_request,
        canonical_key="response.style",
        content="Prefer concise answers.",
        memory_type="semantic",
        scope="user_workspace",
    )

    memory = await create_memory(session, context, workspace.id, body)
    retry = await create_memory(session, context, workspace.id, body)
    assert retry.id == memory.id

    updated = await update_memory(
        session,
        context,
        workspace.id,
        memory.id,
        MemoryPatch(
            client_request_id=uuid4(),
            content="Prefer short answers.",
            importance=0.9,
        ),
    )
    assert updated.content == "Prefer short answers."
    assert updated.importance == 0.9

    forget_request = uuid4()
    await forget_memory(session, context, workspace.id, memory.id, forget_request)
    await forget_memory(session, context, workspace.id, memory.id, forget_request)
    assert (
        await session.execute(
            select(MemorySuppression).where(
                MemorySuppression.user_id == seeded_user.id
            )
        )
    ).scalar_one()
    assert (
        await list_memories(
            session,
            context,
            workspace.id,
            include_history=False,
            limit=10,
            cursor=None,
        )
    ).items == ()
    history = await list_memories(
        session,
        context,
        workspace.id,
        include_history=True,
        limit=10,
        cursor=None,
    )
    assert history.items[0].status == "retracted"

    with pytest.raises(ConflictError, match="forgotten"):
        await create_memory(
            session,
            context,
            workspace.id,
            body.model_copy(
                update={
                    "client_request_id": uuid4(),
                    "content": "Prefer short answers.",
                }
            ),
        )


async def test_memory_preferences_default_to_private_and_get_does_not_write(
    session: AsyncSession,
    seeded_user: User,
    chat_env: dict[str, object],
) -> None:
    workspace = chat_env["workspace"]
    assert isinstance(workspace, Workspace)
    context = memory_context(seeded_user, workspace)

    defaults = await get_preferences(session, context, workspace.id)
    assert defaults.extraction_enabled is False
    assert defaults.semantic_enabled is True
    assert (await session.execute(select(MemoryPreference))).scalar_one_or_none() is None

    updated = await update_preferences(
        session,
        context,
        workspace.id,
        MemoryPreferencePatch(extraction_enabled=True, episodic_enabled=True),
    )
    assert updated.extraction_enabled is True
    assert updated.episodic_enabled is True


async def test_memory_access_requires_workspace_membership(
    session: AsyncSession,
    seeded_user: User,
    chat_env: dict[str, object],
) -> None:
    workspace = chat_env["workspace"]
    assert isinstance(workspace, Workspace)
    context = memory_context(seeded_user, workspace)
    outsider = TenantContext(
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

    with pytest.raises(WorkspaceAccessDenied):
        await list_memories(
            session,
            outsider,
            workspace.id,
            include_history=False,
            limit=10,
            cursor=None,
        )

    assert (
        await list_memories(
            session,
            context,
            workspace.id,
            include_history=False,
            limit=10,
            cursor=None,
        )
    ).items == ()
