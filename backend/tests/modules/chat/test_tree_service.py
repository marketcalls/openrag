import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.errors import ConflictError, NotFoundError
from openrag.modules.auth.models import User
from openrag.modules.chat.events import CitationRef
from openrag.modules.chat.models import Chat, Citation, Message
from openrag.modules.chat.service import (
    NO_ANSWER_TEXT,
    _persist_assistant,
    active_leaf,
    add_message,
    build_tree,
    create_chat,
    delete_chat,
    get_chat,
    list_chats,
    list_messages,
    rename_chat,
    resolve_parent,
)
from openrag.modules.documents.models import (
    Document,
    DocumentChunk,
    DocumentEvidenceSpan,
    DocumentVersion,
)
from openrag.modules.grounding.models import GroundingPolicy
from openrag.modules.models.models import Model
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


async def seed_legacy_source(
    session: AsyncSession,
    context: TenantContext,
    workspace: Workspace,
) -> tuple[Document, DocumentVersion]:
    document = Document(
        org_id=context.org_id,
        workspace_id=workspace.id,
        name="Legacy handbook.pdf",
        filename="legacy-handbook.pdf",
        mime="application/pdf",
        size_bytes=12,
        content_hash="a" * 64,
        storage_key="legacy/handbook.pdf",
        status="indexed",
        created_by=context.user_id,
    )
    session.add(document)
    await session.flush()
    version = DocumentVersion(
        org_id=context.org_id,
        workspace_id=workspace.id,
        document_id=document.id,
        sequence=1,
        version_label="Legacy 1",
        version_key="legacy 1",
        content_hash="a" * 64,
        source_filename="legacy-handbook.pdf",
        source_mime="application/pdf",
        source_size_bytes=12,
        source_storage_key="legacy/handbook.pdf",
        source_page_count=None,
        parser_profile_version="legacy/parser-v1",
        ocr_profile_version="legacy/ocr-unknown-v1",
        chunking_profile_version="legacy/chunking-v1",
        embedding_profile_version="legacy/embedding-v1",
        index_profile_version="legacy/index-v1",
        state="approved",
        provenance_state="legacy_pending",
        created_by=context.user_id,
    )
    session.add(version)
    await session.commit()
    return document, version


async def test_legacy_assistant_and_citation_commit_atomically_with_exact_snapshot(
    session: AsyncSession,
    seeded_user: User,
) -> None:
    context, workspace = await make_ctx(session, seeded_user)
    document, version = await seed_legacy_source(session, context, workspace)
    chat = await create_chat(session, context, workspace_id=workspace.id)
    parent = await add_message(
        session,
        context,
        chat,
        role="user",
        content="What does the handbook say?",
        parent=None,
    )
    assistant = await _persist_assistant(
        session,
        context,
        chat,
        parent=parent,
        content="Use the approved process [1].",
        model_id=None,
        usage=None,
        citations=[
            CitationRef(
                marker=1,
                document_id=str(document.id),
                chunk_ref=f"{document.id}:4:0",
                page=4,
                score=0.9,
            )
        ],
    )
    citation = (
        await session.execute(select(Citation).where(Citation.message_id == assistant.id))
    ).scalar_one()
    assert (assistant.org_id, assistant.workspace_id, assistant.answer_status) == (
        context.org_id,
        workspace.id,
        None,
    )
    assert (
        citation.org_id,
        citation.workspace_id,
        citation.document_version_id,
        citation.document_name,
        citation.version_label,
        citation.section_path,
        citation.content_hash,
        citation.claim_ids,
        citation.evidence_span_id,
        citation.verification_state,
    ) == (
        context.org_id,
        workspace.id,
        version.id,
        document.name,
        "Legacy 1",
        ["Legacy import"],
        "legacy-unverified",
        [],
        None,
        "legacy_unverified",
    )
    serialized = build_tree([parent, assistant], {assistant.id: [citation]})
    source = serialized[0].children[0].citations[0].model_dump()
    assert source == {
        "marker": 1,
        "document_id": document.id,
        "chunk_ref": f"{document.id}:4:0",
        "page": 4,
        "score": 0.9,
        "document_name": "Legacy handbook.pdf",
        "version_label": "Legacy 1",
        "section_label": "Legacy import",
        "section_path": ["Legacy import"],
        "locator_kind": "page",
        "locator_label": "4",
        "verification_state": "legacy_unverified",
    }


async def test_legacy_citation_failure_rolls_back_assistant(
    session: AsyncSession,
    seeded_user: User,
) -> None:
    context, workspace = await make_ctx(session, seeded_user)
    document, _version = await seed_legacy_source(session, context, workspace)
    chat = await create_chat(session, context, workspace_id=workspace.id)
    parent = await add_message(
        session,
        context,
        chat,
        role="user",
        content="question",
        parent=None,
    )
    parent_id = parent.id

    with pytest.raises(IntegrityError):
        await _persist_assistant(
            session,
            context,
            chat,
            parent=parent,
            content="invalid citation",
            model_id=None,
            usage=None,
            citations=[
                CitationRef(
                    marker=1,
                    document_id=str(document.id),
                    chunk_ref="invalid:page",
                    page=0,
                    score=0.9,
                )
            ],
        )
    assistant_count = (
        await session.execute(
            select(func.count())
            .select_from(Message)
            .where(Message.parent_message_id == parent_id)
        )
    ).scalar_one()
    assert assistant_count == 0


async def test_no_evidence_is_server_owned_refusal_with_zero_citations(
    session: AsyncSession,
    seeded_user: User,
) -> None:
    context, workspace = await make_ctx(session, seeded_user)
    chat = await create_chat(session, context, workspace_id=workspace.id)
    parent = await add_message(
        session,
        context,
        chat,
        role="user",
        content="unknown",
        parent=None,
    )
    assistant = await _persist_assistant(
        session,
        context,
        chat,
        parent=parent,
        content="Insufficient evidence.",
        model_id=None,
        usage=None,
        citations=[],
    )
    assert (assistant.answer_status, assistant.refusal_reason) == (
        "refused",
        "below_threshold",
    )
    assert assistant.content == NO_ANSWER_TEXT
    assert (
        await session.execute(
            select(func.count()).select_from(Citation).where(Citation.message_id == assistant.id)
        )
    ).scalar_one() == 0


async def test_authority_activation_fails_closed_without_active_policy(
    session: AsyncSession,
    seeded_user: User,
) -> None:
    context, workspace = await make_ctx(session, seeded_user)
    document, _version = await seed_legacy_source(session, context, workspace)
    workspace.document_authority_enabled = True
    await session.commit()
    chat = await create_chat(session, context, workspace_id=workspace.id)
    parent = await add_message(
        session,
        context,
        chat,
        role="user",
        content="question",
        parent=None,
    )
    assistant = await _persist_assistant(
        session,
        context,
        chat,
        parent=parent,
        content="legacy answer [1].",
        model_id=None,
        usage=None,
        citations=[
            CitationRef(
                marker=1,
                document_id=str(document.id),
                chunk_ref="legacy:1",
                page=1,
                score=0.8,
            )
        ],
    )

    assert assistant.answer_status == "refused"
    assert assistant.refusal_reason == "grounding_policy_unavailable"
    assert assistant.content == NO_ANSWER_TEXT


async def test_authority_answer_revalidates_and_persists_complete_snapshot(
    session: AsyncSession,
    seeded_user: User,
) -> None:
    context, workspace = await make_ctx(session, seeded_user)
    workspace.document_authority_enabled = True
    verifier = Model(
        litellm_model_name="openai/verifier",
        display_name="Verifier",
        provider_kind="litellm",
        supports_chat_completion=True,
        supports_structured_json=True,
        supports_verifier=True,
        provider_preset_version="preset-v1",
    )
    session.add(verifier)
    await session.flush()
    policy = GroundingPolicy(
        org_id=context.org_id,
        workspace_id=workspace.id,
        policy_version=1,
        verifier_model_id=verifier.id,
        binding_revision="binding-v1",
        provider_preset_version="preset-v1",
        credential_fingerprint="credential-fingerprint-v1",
        entailment_threshold=0.9,
        calibration_dataset_version="dataset-v1",
        calibration_dataset_hash="a" * 64,
        calibration_sample_count=10,
        status="active",
        created_by=context.user_id,
    )
    document = Document(
        org_id=context.org_id,
        workspace_id=workspace.id,
        name="HSE Manual.pdf",
        filename="hse-manual.pdf",
        mime="application/pdf",
        size_bytes=100,
        content_hash="b" * 64,
        storage_key="hse/manual.pdf",
        status="indexed",
        created_by=context.user_id,
    )
    session.add_all([policy, document])
    await session.flush()
    version = DocumentVersion(
        org_id=context.org_id,
        workspace_id=workspace.id,
        document_id=document.id,
        sequence=2,
        version_label="Approved 2",
        version_key="approved 2",
        content_hash="c" * 64,
        source_filename="hse-manual.pdf",
        source_mime="application/pdf",
        source_size_bytes=100,
        source_storage_key="versions/hse/manual.pdf",
        source_page_count=8,
        parser_profile_version="docling/v1",
        ocr_profile_version="ocr/v1",
        chunking_profile_version="semantic/v1",
        embedding_profile_version="bge-m3/v1",
        index_profile_version="hybrid/v1",
        state="approved",
        provenance_state="ready",
        created_by=context.user_id,
    )
    session.add(version)
    await session.flush()
    chunk = DocumentChunk(
        org_id=context.org_id,
        document_version_id=version.id,
        ordinal=0,
        text="Inspect PPE before every shift.",
        token_count=7,
        page_start=5,
        page_end=5,
        section_path=["Safety", "PPE"],
        content_hash="d" * 64,
        chunking_profile_version="semantic/v1",
        embedding_profile_version="bge-m3/v1",
    )
    session.add(chunk)
    await session.flush()
    span = DocumentEvidenceSpan(
        org_id=context.org_id,
        document_version_id=version.id,
        chunk_id=chunk.id,
        page_number=5,
        locator_kind="page",
        locator_label="5",
        section_path=["Safety", "PPE"],
        content_hash="d" * 64,
        ordinal=0,
        token_count=7,
        artifact_byte_start=0,
        artifact_byte_end=len(chunk.text),
    )
    session.add(span)
    await session.commit()
    chat = await create_chat(session, context, workspace_id=workspace.id)
    parent = await add_message(
        session,
        context,
        chat,
        role="user",
        content="When should PPE be inspected?",
        parent=None,
    )

    assistant = await _persist_assistant(
        session,
        context,
        chat,
        parent=parent,
        content="Inspect PPE before every shift [1].",
        model_id=None,
        usage=None,
        citations=[
            CitationRef(
                marker=1,
                document_id=str(document.id),
                chunk_ref=str(span.id),
                page=5,
                score=0.92,
                document_version_id=str(version.id),
                evidence_span_id=str(span.id),
                content_hash=span.content_hash,
                dense_score=0.94,
                sparse_score=0.71,
                fused_score=0.92,
            )
        ],
    )
    citation = await session.scalar(
        select(Citation).where(Citation.message_id == assistant.id)
    )

    assert assistant.answer_status == "grounded"
    assert assistant.grounding_policy_id == policy.id
    assert citation is not None
    assert citation.document_version_id == version.id
    assert citation.evidence_span_id == span.id
    assert citation.document_name == "HSE Manual.pdf"
    assert citation.version_label == "Approved 2"
    assert citation.section_label == "Safety / PPE"
    assert citation.locator_label == "5"
    assert citation.content_hash == "d" * 64
    assert citation.verification_state == "marker_bound"
    assert citation.claim_ids and len(citation.claim_ids[0]) == 64


def test_message_scope_is_non_nullable_after_task_two_backfill() -> None:
    assert Message.__table__.c.org_id.nullable is False
    assert Message.__table__.c.workspace_id.nullable is False
