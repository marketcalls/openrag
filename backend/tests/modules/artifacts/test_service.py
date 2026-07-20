import copy
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.errors import ConflictError, InvalidRequestError
from openrag.modules.artifacts.models import MessageArtifact
from openrag.modules.artifacts.schemas import AnalyticsResponseV1
from openrag.modules.artifacts.service import (
    list_message_artifacts,
    persist_analytics_artifact,
    serialize_analytics_artifact,
)
from openrag.modules.auth.models import User
from openrag.modules.chat.models import Chat, Citation, Message
from openrag.modules.documents.models import Document
from openrag.modules.tenancy.authorization import AuthorizationSnapshot
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import Workspace


def valid_artifact() -> AnalyticsResponseV1:
    return AnalyticsResponseV1.model_validate(
        {
            "schema_version": "analytics.v1",
            "title": "Revenue dashboard",
            "subtitle": "Approved Q4 revenue summary",
            "kpis": [
                {
                    "label": "Q4 revenue",
                    "value": "$4.83M",
                    "trend": "up",
                    "source_markers": [1],
                }
            ],
            "blocks": [
                {
                    "kind": "bar_chart",
                    "title": "Monthly revenue",
                    "x_label": "Month",
                    "y_label": "Revenue in millions",
                    "categories": ["October", "November", "December"],
                    "series": [
                        {"name": "Revenue", "values": [1.42, 1.57, 1.84]}
                    ],
                    "source_markers": [1],
                },
                {
                    "kind": "table",
                    "title": "Revenue summary",
                    "columns": [
                        {"key": "month", "label": "Month", "format": "text"},
                        {
                            "key": "revenue",
                            "label": "Revenue",
                            "format": "currency",
                        },
                    ],
                    "rows": [
                        {"month": "October", "revenue": 1.42},
                        {"month": "November", "revenue": 1.57},
                    ],
                    "source_markers": [1],
                },
            ],
            "suggested_followups": ["Break this down by product line"],
        }
    )


def context_for(user: User, workspace: Workspace) -> TenantContext:
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


async def seed_grounded_message(
    session: AsyncSession,
    user: User,
    workspace: Workspace,
    document: Document,
) -> Message:
    chat = Chat(
        org_id=user.org_id,
        workspace_id=workspace.id,
        user_id=user.id,
        title="Revenue analysis",
    )
    session.add(chat)
    await session.flush()
    message = Message(
        org_id=user.org_id,
        workspace_id=workspace.id,
        chat_id=chat.id,
        role="assistant",
        content="Q4 revenue was $4.83M [1].",
        answer_status="grounded",
    )
    session.add(message)
    await session.flush()
    session.add(
        Citation(
            org_id=user.org_id,
            workspace_id=workspace.id,
            message_id=message.id,
            document_id=document.id,
            document_version_id=document.id,
            chunk_ref="legacy:1",
            page=1,
            score=0.92,
            marker=1,
            document_name=document.name,
            version_label="Legacy 1",
            section_label="Legacy import",
            section_path=["Legacy import"],
            locator_kind="page",
            locator_label="1",
            content_hash="legacy-unverified",
            claim_ids=[],
            verification_state="legacy_unverified",
        )
    )
    await session.flush()
    return message


def test_analytics_serialization_is_canonical_and_content_addressed() -> None:
    artifact = valid_artifact()

    first = serialize_analytics_artifact(artifact)
    second = serialize_analytics_artifact(
        AnalyticsResponseV1.model_validate(
            copy.deepcopy(artifact.model_dump(mode="json"))
        )
    )

    assert first.payload == second.payload
    assert first.encoded == second.encoded
    assert first.content_hash == second.content_hash
    assert len(first.content_hash) == 64
    assert first.encoded.startswith(b'{"blocks":')


def test_message_artifact_model_has_exact_tenant_message_contract() -> None:
    table = MessageArtifact.__table__
    assert {column.name for column in table.primary_key.columns} == {"id"}
    assert {
        column.name for column in table.columns if not column.nullable
    } >= {
        "org_id",
        "workspace_id",
        "message_id",
        "kind",
        "schema_version",
        "payload",
        "content_hash",
        "created_at",
    }
    assert any(
        tuple(column.name for column in constraint.columns)
        == ("org_id", "workspace_id", "message_id", "kind")
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    )
    assert any(
        constraint.name == "fk_message_artifacts_org_workspace_message"
        and tuple(column.name for column in constraint.columns)
        == ("org_id", "workspace_id", "message_id")
        for constraint in table.foreign_key_constraints
    )


async def test_artifact_persistence_is_scoped_idempotent_and_strict(
    session: AsyncSession,
    seeded_user: User,
    chat_env: dict[str, object],
) -> None:
    workspace = chat_env["workspace"]
    document = chat_env["document"]
    assert isinstance(workspace, Workspace)
    assert isinstance(document, Document)
    context = context_for(seeded_user, workspace)
    message = await seed_grounded_message(
        session,
        seeded_user,
        workspace,
        document,
    )
    artifact = valid_artifact()

    first = await persist_analytics_artifact(
        session,
        context,
        message.id,
        artifact,
    )
    retry = await persist_analytics_artifact(
        session,
        context,
        message.id,
        artifact,
    )

    assert retry.id == first.id
    assert retry.content_hash == first.content_hash
    listed = await list_message_artifacts(session, context, [message.id])
    assert list(listed) == [message.id]
    assert listed[message.id][0].artifact == artifact
    assert listed[message.id][0].content_hash == first.content_hash

    outsider_id = uuid4()
    outsider = TenantContext(
        user_id=outsider_id,
        org_id=seeded_user.org_id,
        authorization=AuthorizationSnapshot(
            user_id=outsider_id,
            org_id=seeded_user.org_id,
            is_platform_superadmin=False,
            org_permissions=frozenset({"chat.use"}),
            workspace_permissions={},
            workspace_ids=frozenset({workspace.id}),
        ),
    )
    assert await list_message_artifacts(session, outsider, [message.id]) == {}


async def test_artifact_persistence_rejects_missing_markers_and_mutation(
    session: AsyncSession,
    seeded_user: User,
    chat_env: dict[str, object],
) -> None:
    workspace = chat_env["workspace"]
    document = chat_env["document"]
    assert isinstance(workspace, Workspace)
    assert isinstance(document, Document)
    context = context_for(seeded_user, workspace)
    message = await seed_grounded_message(
        session,
        seeded_user,
        workspace,
        document,
    )
    artifact = valid_artifact()
    invalid_payload = artifact.model_dump(mode="json")
    invalid_payload["kpis"][0]["source_markers"] = [2]
    invalid = AnalyticsResponseV1.model_validate(invalid_payload)

    with pytest.raises(InvalidRequestError, match="unavailable citation"):
        await persist_analytics_artifact(
            session,
            context,
            message.id,
            invalid,
        )

    stored = await persist_analytics_artifact(
        session,
        context,
        message.id,
        artifact,
    )
    changed = artifact.model_copy(update={"title": "Changed dashboard"})
    with pytest.raises(ConflictError, match="already exists"):
        await persist_analytics_artifact(
            session,
            context,
            message.id,
            changed,
        )
    assert stored.content_hash == (
        await list_message_artifacts(session, context, [message.id])
    )[message.id][0].content_hash


async def test_artifact_persistence_rejects_unowned_or_ungrounded_messages(
    session: AsyncSession,
    seeded_user: User,
    chat_env: dict[str, object],
) -> None:
    workspace = chat_env["workspace"]
    document = chat_env["document"]
    assert isinstance(workspace, Workspace)
    assert isinstance(document, Document)
    context = context_for(seeded_user, workspace)
    message = await seed_grounded_message(
        session,
        seeded_user,
        workspace,
        document,
    )
    message.answer_status = "refused"
    message.refusal_reason = "insufficient_evidence"
    await session.flush()

    with pytest.raises(InvalidRequestError, match="grounded assistant"):
        await persist_analytics_artifact(
            session,
            context,
            message.id,
            valid_artifact(),
        )


async def test_artifact_reads_reject_unbounded_message_lists(
    session: AsyncSession,
    seeded_user: User,
    chat_env: dict[str, object],
) -> None:
    workspace = chat_env["workspace"]
    assert isinstance(workspace, Workspace)
    context = context_for(seeded_user, workspace)

    with pytest.raises(ValueError, match="message list"):
        await list_message_artifacts(
            session,
            context,
            [uuid4() for _ in range(501)],
        )
