from uuid import uuid4

import pytest
from sqlalchemy import func, insert, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.chat.models import Chat, Citation, Message
from openrag.modules.documents.models import (
    Document,
    DocumentChunk,
    DocumentEvidenceSpan,
    DocumentVersion,
)
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
        name="Source document",
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
        grounding_policy_id=uuid4(),
        grounding_policy_version=3,
        verifier_model_id=uuid4(),
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
    version = DocumentVersion(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        document_id=document.id,
        sequence=1,
        version_label="Rev 1",
        version_key="rev 1",
        content_hash="a" * 64,
        source_filename="conflict.pdf",
        source_mime="application/pdf",
        source_size_bytes=10,
        source_storage_key="versions/conflict/source",
        source_page_count=4,
        parser_profile_version="docling/v1",
        ocr_profile_version="none/v1",
        chunking_profile_version="semantic/v1",
        embedding_profile_version="bge-m3/v1",
        index_profile_version="hybrid/v1",
        created_by=seeded_user.id,
    )
    session.add(version)
    await session.flush()
    chunks = [
        DocumentChunk(
            org_id=seeded_user.org_id,
            document_version_id=version.id,
            ordinal=index,
            text=text,
            token_count=4,
            page_start=page,
            page_end=page,
            section_path=["Emergency", section],
            content_hash=hash_character * 64,
            chunking_profile_version="semantic/v1",
            embedding_profile_version="bge-m3/v1",
        )
        for index, (text, page, section, hash_character) in enumerate(
            [
                ("First approved requirement", 2, "Applicability", "b"),
                ("Conflicting approved amendment", 4, "Amendment", "c"),
            ]
        )
    ]
    session.add_all(chunks)
    await session.flush()
    spans = [
        DocumentEvidenceSpan(
            org_id=seeded_user.org_id,
            document_version_id=version.id,
            chunk_id=chunk.id,
            page_number=page,
            locator_kind="page",
            locator_label=str(page),
            section_path=["Emergency", section],
            content_hash=hash_character * 64,
            ordinal=index,
            token_count=4,
            artifact_byte_start=index * 32,
            artifact_byte_end=index * 32 + len(chunk.text),
        )
        for index, (chunk, page, section, hash_character) in enumerate(
            [
                (chunks[0], 2, "Applicability", "b"),
                (chunks[1], 4, "Amendment", "c"),
            ]
        )
    ]
    session.add_all(spans)
    await session.flush()
    citation = Citation(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        message_id=conflict.id,
        document_id=document.id,
        document_version_id=version.id,
        evidence_span_id=spans[0].id,
        chunk_ref="authority:span:1",
        page=2,
        score=0.91,
        marker=1,
        section_path=["Emergency", "Applicability"],
        claim_ids=[str(conflict.id), str(user_message.id)],
        document_name=document.name,
        version_label=version.version_label,
        content_hash=spans[0].content_hash,
        prompt_contract_version="grounded-v1",
        grounding_policy_id=conflict.grounding_policy_id,
        grounding_policy_version=3,
        verifier_model_id=conflict.verifier_model_id,
        provider_preset_version="litellm-v1",
        binding_revision="binding-4",
        credential_fingerprint="sha256:credential",
    )
    second_citation = Citation(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        message_id=conflict.id,
        document_id=document.id,
        document_version_id=version.id,
        evidence_span_id=spans[1].id,
        chunk_ref="authority:span:2",
        page=4,
        score=0.89,
        marker=2,
        section_path=["Emergency", "Amendment"],
        claim_ids=[str(conflict.id)],
        document_name=document.name,
        version_label=version.version_label,
        content_hash=spans[1].content_hash,
        prompt_contract_version="grounded-v1",
        grounding_policy_id=conflict.grounding_policy_id,
        grounding_policy_version=3,
        verifier_model_id=conflict.verifier_model_id,
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


def test_citation_section_input_is_nfkc_normalized() -> None:
    citation = Citation(
        message_id=uuid4(),
        document_id=uuid4(),
        chunk_ref="authority:test",
        page=1,
        score=0.9,
        marker=1,
        section_path=["  Ｅmergency  ", "Ｅvacuation"],
        claim_ids=[str(uuid4())],
    )
    assert citation.section_path == ["Emergency", "Evacuation"]


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("section_path", [123]),
        ("section_path", [{"heading": "object"}]),
        ("section_path", [""]),
        ("section_path", ["x" * 201]),
        ("section_path", ["Valid", 3]),
        ("claim_ids", [123]),
        ("claim_ids", [{"claim": "object"}]),
        ("claim_ids", [""]),
        ("claim_ids", ["x" * 65]),
        ("claim_ids", [str(uuid4()), 3]),
    ],
)
async def test_citation_jsonb_elements_are_bounded_strings_in_postgresql(
    session: AsyncSession,
    seeded_user: User,
    field: str,
    invalid_value: list[object],
) -> None:
    workspace = Workspace(org_id=seeded_user.org_id, name=f"Invalid JSON {uuid4()}")
    session.add(workspace)
    await session.flush()
    chat = Chat(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        user_id=seeded_user.id,
    )
    document = Document(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        name="Invalid citation JSON",
        filename="invalid.json.pdf",
        mime="application/pdf",
        size_bytes=1,
        content_hash=f"legacy-{uuid4()}",
        storage_key=f"legacy/{uuid4()}",
        created_by=seeded_user.id,
    )
    session.add_all([chat, document])
    await session.flush()
    message = Message(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        chat_id=chat.id,
        role="assistant",
        content="invalid citation",
    )
    session.add(message)
    await session.flush()
    values: dict[str, object] = {
        "org_id": seeded_user.org_id,
        "workspace_id": workspace.id,
        "message_id": message.id,
        "document_id": document.id,
        "chunk_ref": "authority:invalid",
        "page": 1,
        "score": 0.5,
        "marker": 1,
        "section_path": ["Valid"],
        "claim_ids": [str(uuid4())],
    }
    values[field] = invalid_value
    with pytest.raises(IntegrityError):
        await session.execute(insert(Citation).values(**values))
        await session.commit()


async def test_citation_content_hash_requires_lowercase_sha256(
    session: AsyncSession,
    seeded_user: User,
) -> None:
    workspace = Workspace(org_id=seeded_user.org_id, name="Citation digest")
    session.add(workspace)
    await session.flush()
    chat = Chat(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        user_id=seeded_user.id,
    )
    document = Document(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        name="Citation digest",
        filename="digest.pdf",
        mime="application/pdf",
        size_bytes=1,
        content_hash="legacy-citation-digest",
        storage_key="legacy/digest.pdf",
        created_by=seeded_user.id,
    )
    session.add_all([chat, document])
    await session.flush()
    message = Message(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        chat_id=chat.id,
        role="assistant",
        content="digest",
    )
    session.add(message)
    await session.flush()
    session.add(
        Citation(
            org_id=seeded_user.org_id,
            workspace_id=workspace.id,
            message_id=message.id,
            document_id=document.id,
            chunk_ref="authority:digest",
            page=1,
            score=0.5,
            marker=1,
            content_hash="g" * 64,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
