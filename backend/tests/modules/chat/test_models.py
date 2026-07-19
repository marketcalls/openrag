import pytest
from sqlalchemy import func, inspect, select
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


async def test_cited_conflict_and_multi_claim_snapshot_shape(
    session: AsyncSession,
    seeded_user: User,
) -> None:
    workspace = Workspace(org_id=seeded_user.org_id, name="Conflict Workspace")
    session.add(workspace)
    await session.flush()
    chat = Chat(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        user_id=seeded_user.id,
    )
    session.add(chat)
    await session.flush()
    user_message = Message(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        chat_id=chat.id,
        role="user",
        content="Which policy applies?",
    )
    session.add(user_message)
    await session.flush()
    conflict = Message(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        chat_id=chat.id,
        parent_message_id=user_message.id,
        role="assistant",
        content="The approved sources conflict.",
        answer_status="cited_conflict",
        prompt_contract_version="grounded-v1",
        grounding_policy_version=3,
        provider_preset_version="litellm-v1",
        binding_revision="binding-4",
        credential_fingerprint="sha256:credential",
    )
    session.add(conflict)
    await session.flush()
    document = Document(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        name="Conflicting policy",
        filename="conflict.pdf",
        mime="application/pdf",
        size_bytes=10,
        content_hash="conflicting-citation-document",
        storage_key="conflict/source.pdf",
        owner_id=seeded_user.id,
        created_by=seeded_user.id,
    )
    session.add(document)
    await session.flush()
    citation = Citation(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        message_id=conflict.id,
        document_id=document.id,
        chunk_ref="authority:span:1",
        page=2,
        score=0.91,
        marker=1,
        section_path=["Emergency", "Applicability"],
        claim_ids=[str(conflict.id), str(user_message.id)],
        prompt_contract_version="grounded-v1",
        grounding_policy_version=3,
        provider_preset_version="litellm-v1",
        binding_revision="binding-4",
        credential_fingerprint="sha256:credential",
    )
    second_document = Document(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        name="Conflicting policy amendment",
        filename="conflict-amendment.pdf",
        mime="application/pdf",
        size_bytes=11,
        content_hash="conflicting-citation-amendment",
        storage_key="conflict/amendment.pdf",
        owner_id=seeded_user.id,
        created_by=seeded_user.id,
    )
    session.add(second_document)
    await session.flush()
    second_citation = Citation(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        message_id=conflict.id,
        document_id=second_document.id,
        chunk_ref="authority:span:2",
        page=4,
        score=0.89,
        marker=2,
        section_path=["Emergency", "Amendment"],
        claim_ids=[str(conflict.id)],
        prompt_contract_version="grounded-v1",
        grounding_policy_version=3,
        provider_preset_version="litellm-v1",
        binding_revision="binding-4",
        credential_fingerprint="sha256:credential",
    )
    session.add_all([citation, second_citation])
    await session.commit()

    assert conflict.answer_status == "cited_conflict"
    assert conflict.prompt_contract_version == "grounded-v1"
    assert citation.claim_ids == [str(conflict.id), str(user_message.id)]
    assert citation.section_path == ["Emergency", "Applicability"]
    assert second_citation.marker == 2
    assert set(inspect(Citation).columns.keys()) >= {
        "section_path",
        "claim_ids",
        "prompt_contract_version",
        "grounding_policy_version",
        "verifier_model_id",
        "provider_preset_version",
        "binding_revision",
        "credential_fingerprint",
    }


async def test_refusal_state_is_persisted_without_citation(
    session: AsyncSession,
    seeded_user: User,
) -> None:
    workspace = Workspace(org_id=seeded_user.org_id, name="Refusal Workspace")
    session.add(workspace)
    await session.flush()
    chat = Chat(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        user_id=seeded_user.id,
    )
    session.add(chat)
    await session.flush()
    refused = Message(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        chat_id=chat.id,
        role="assistant",
        content="Insufficient evidence.",
        answer_status="refused",
        refusal_reason="below_threshold",
    )
    session.add(refused)
    await session.commit()
    assert refused.answer_status == "refused"
    citation_count = (
        await session.execute(
            select(func.count()).select_from(Citation).where(Citation.message_id == refused.id)
        )
    ).scalar_one()
    assert citation_count == 0


@pytest.mark.parametrize(
    ("answer_status", "refusal_reason"),
    [("verified", None), ("refused", None), ("grounded", "below_threshold")],
)
async def test_invalid_answer_state_combinations_are_rejected(
    session: AsyncSession,
    seeded_user: User,
    answer_status: str,
    refusal_reason: str | None,
) -> None:
    workspace = Workspace(org_id=seeded_user.org_id, name=f"Invalid {answer_status}")
    session.add(workspace)
    await session.flush()
    chat = Chat(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        user_id=seeded_user.id,
    )
    session.add(chat)
    await session.flush()
    session.add(
        Message(
            org_id=seeded_user.org_id,
            workspace_id=workspace.id,
            chat_id=chat.id,
            role="assistant",
            content="invalid",
            answer_status=answer_status,
            refusal_reason=refusal_reason,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
