"""Tenant-scoped conversation trees and retrieval-backed reply streaming."""

from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import Settings
from openrag.core.db import naive_utc
from openrag.core.errors import ConflictError, NotFoundError, UpstreamError
from openrag.modules.chat.claims import ClaimBindingResult, bind_cited_claims
from openrag.modules.chat.events import (
    CitationRef,
    SourceRef,
    SSEEvent,
    citations_event,
    done_event,
    error_event,
    retrieval_started_event,
    route_selected_event,
    sources_event,
    token_event,
)
from openrag.modules.chat.llm import LLMDelta, LLMStreamer, LLMUsage
from openrag.modules.chat.models import Chat, Citation, Message
from openrag.modules.chat.prompting import (
    PromptSource,
    build_conversation_messages,
    build_direct_messages,
    build_messages,
    parse_citation_markers,
)
from openrag.modules.chat.schemas import ChatTreeOut, CitationOut, MessageNode
from openrag.modules.documents import service as documents_service
from openrag.modules.documents.lifecycle import (
    LEGACY_CITATION_CONTENT_HASH,
    LEGACY_CITATION_SECTION,
    LEGACY_CITATION_VERIFICATION_STATE,
    LEGACY_VERSION_KEY,
    LEGACY_VERSION_LABEL,
)
from openrag.modules.documents.models import Document, DocumentVersion
from openrag.modules.grounding.models import GroundingPolicy
from openrag.modules.models.models import Model
from openrag.modules.orchestration.routing import QueryRoute, decide_route
from openrag.modules.retrieval.authority import (
    AuthorizedEvidence,
    CandidateIdentity,
    revalidate_candidates,
)
from openrag.modules.retrieval.service import RetrievalResult
from openrag.modules.tenancy import service as tenancy_service
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import Workspace

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
PROMPT_CONTRACT_VERSION = "openrag-grounded-v1"
AUTHORITY_VERIFICATION_STATE = "marker_bound"
CLARIFY_TEXT = (
    "What would you like me to explain? Please mention the document, topic, "
    "or earlier question you mean."
)


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
    message = await build_message(
        session,
        context,
        chat,
        role=role,
        content=content,
        parent=parent,
        model_id=model_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    await session.commit()
    return message


async def build_message(
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
        org_id=context.org_id,
        workspace_id=chat.workspace_id,
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
    if result.evidence:
        return [
            SourceRef(
                marker=marker,
                document_id=str(evidence.document_id),
                filename=evidence.document_name,
                page=evidence.page_number,
                chunk_index=evidence.chunk_index,
                score=evidence.fused_score,
                snippet=evidence.text[:_SNIPPET_CHARS],
                document_version_id=str(evidence.document_version_id),
                evidence_span_id=str(evidence.evidence_span_id),
                version_label=evidence.version_label,
                section_label=" / ".join(evidence.section_path),
                section_path=list(evidence.section_path),
                locator_kind=evidence.locator_kind,
                locator_label=evidence.locator_label,
                content_hash=evidence.content_hash,
                dense_score=evidence.dense_score,
                sparse_score=evidence.sparse_score,
                fused_score=evidence.fused_score,
                rerank_score=evidence.rerank_score,
            )
            for marker, evidence in enumerate(result.evidence, start=1)
        ]

    filenames: dict[UUID, str] = {}
    references: list[SourceRef] = []
    for marker, chunk in enumerate(result.chunks, start=1):
        if chunk.document_id not in filenames:
            document = await documents_service.get_document_checked(
                session,
                context,
                chunk.document_id,
            )
            filenames[chunk.document_id] = document.filename or document.name
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
    refusal_reason: str = "below_threshold",
) -> Message:
    await tenancy_service.get_workspace(
        session,
        context,
        chat.workspace_id,
        "chat.use",
    )
    try:
        workspace = (
            await session.execute(
                select(Workspace)
                .where(
                    Workspace.org_id == context.org_id,
                    Workspace.id == chat.workspace_id,
                )
                .with_for_update()
            )
        ).scalar_one()
        authority_sources: list[tuple[CitationRef, AuthorizedEvidence]] = []
        policy: GroundingPolicy | None = None
        bindings: ClaimBindingResult | None = None
        if citations and workspace.document_authority_enabled:
            now = datetime.now(UTC)
            database_now = now.replace(tzinfo=None)
            policy = await session.scalar(
                select(GroundingPolicy).where(
                    GroundingPolicy.org_id == context.org_id,
                    GroundingPolicy.workspace_id == chat.workspace_id,
                    GroundingPolicy.status == "active",
                    or_(
                        GroundingPolicy.effective_at.is_(None),
                        GroundingPolicy.effective_at <= database_now,
                    ),
                    or_(
                        GroundingPolicy.expires_at.is_(None),
                        GroundingPolicy.expires_at > database_now,
                    ),
                )
            )
            if policy is None:
                citations = []
                refusal_reason = "grounding_policy_unavailable"
            else:
                markers = [citation.marker for citation in citations]
                if len(markers) != len(set(markers)) or min(markers) < 1:
                    citations = []
                    refusal_reason = "invalid_marker"
                else:
                    bindings = bind_cited_claims(content, max_marker=max(markers))
                    if not bindings.valid or set(bindings.by_marker) != set(markers):
                        citations = []
                        refusal_reason = bindings.reason_code or "incomplete_claim_binding"

            candidates: list[CandidateIdentity] = []
            if citations:
                for citation in citations:
                    if (
                        citation.document_version_id is None
                        or citation.evidence_span_id is None
                        or citation.content_hash is None
                    ):
                        candidates = []
                        citations = []
                        refusal_reason = "authority_identity_missing"
                        break
                    try:
                        candidates.append(
                            CandidateIdentity(
                                document_version_id=UUID(citation.document_version_id),
                                evidence_span_id=UUID(citation.evidence_span_id),
                                content_hash=citation.content_hash,
                                fused_score=(
                                    citation.fused_score
                                    if citation.fused_score is not None
                                    else citation.score
                                ),
                                dense_score=citation.dense_score,
                                sparse_score=citation.sparse_score,
                            )
                        )
                    except ValueError:
                        candidates = []
                        citations = []
                        refusal_reason = "authority_identity_invalid"
                        break
            if citations:
                authorized = await revalidate_candidates(
                    session,
                    context,
                    chat.workspace_id,
                    candidates,
                    now=now,
                )
                by_span = {item.evidence_span_id: item for item in authorized}
                for citation, candidate in zip(citations, candidates, strict=True):
                    evidence = by_span.get(candidate.evidence_span_id)
                    if (
                        evidence is None
                        or str(evidence.document_id) != citation.document_id
                    ):
                        authority_sources = []
                        citations = []
                        refusal_reason = "authority_changed"
                        break
                    authority_sources.append((citation, evidence))

        legacy_sources: list[tuple[CitationRef, Document, DocumentVersion]] = []
        if not workspace.document_authority_enabled:
            for citation in citations:
                document_id = UUID(citation.document_id)
                row = (
                    await session.execute(
                        select(Document, DocumentVersion)
                        .join(
                            DocumentVersion,
                            DocumentVersion.document_id == Document.id,
                        )
                        .where(
                            Document.org_id == context.org_id,
                            Document.workspace_id == chat.workspace_id,
                            Document.id == document_id,
                            DocumentVersion.org_id == context.org_id,
                            DocumentVersion.workspace_id == chat.workspace_id,
                            DocumentVersion.sequence == 1,
                            DocumentVersion.version_label == LEGACY_VERSION_LABEL,
                            DocumentVersion.version_key == LEGACY_VERSION_KEY,
                        )
                    )
                ).one_or_none()
                if row is None:
                    raise ConflictError("legacy source is not migration-ready")
                document, version = row
                legacy_sources.append((citation, document, version))

        persisted_content = content if citations else NO_ANSWER_TEXT
        message = await build_message(
            session,
            context,
            chat,
            role=ROLE_ASSISTANT,
            content=persisted_content,
            parent=parent,
            model_id=model_id,
            prompt_tokens=usage.prompt_tokens if usage is not None else None,
            completion_tokens=(
                usage.completion_tokens if usage is not None else None
            ),
        )
        if citations and workspace.document_authority_enabled:
            assert policy is not None
            message.answer_status = "grounded"
            message.refusal_reason = None
            message.grounding_policy_id = policy.id
            message.grounding_policy_version = policy.policy_version
            message.verifier_model_id = policy.verifier_model_id
            message.prompt_contract_version = PROMPT_CONTRACT_VERSION
            message.provider_preset_version = policy.provider_preset_version
            message.binding_revision = policy.binding_revision
            message.credential_fingerprint = policy.credential_fingerprint
        elif citations:
            message.answer_status = None
            message.refusal_reason = None
        else:
            message.answer_status = "refused"
            message.refusal_reason = refusal_reason
        await session.flush()

        for citation, document, version in legacy_sources:
            session.add(
                Citation(
                    org_id=context.org_id,
                    workspace_id=chat.workspace_id,
                    message_id=message.id,
                    document_id=document.id,
                    document_version_id=version.id,
                    evidence_span_id=None,
                    chunk_ref=citation.chunk_ref,
                    page=citation.page,
                    score=citation.score,
                    marker=citation.marker,
                    document_name=document.name,
                    version_label=LEGACY_VERSION_LABEL,
                    section_label=LEGACY_CITATION_SECTION,
                    section_path=[LEGACY_CITATION_SECTION],
                    locator_kind="page",
                    locator_label=str(citation.page),
                    content_hash=LEGACY_CITATION_CONTENT_HASH,
                    claim_ids=[],
                    verification_state=LEGACY_CITATION_VERIFICATION_STATE,
                )
            )
        if authority_sources:
            assert policy is not None
            assert bindings is not None
            for citation, evidence in authority_sources:
                session.add(
                    Citation(
                        org_id=context.org_id,
                        workspace_id=chat.workspace_id,
                        message_id=message.id,
                        document_id=evidence.document_id,
                        document_version_id=evidence.document_version_id,
                        evidence_span_id=evidence.evidence_span_id,
                        chunk_ref=evidence.chunk_ref,
                        page=evidence.page_number,
                        score=evidence.fused_score,
                        marker=citation.marker,
                        document_name=evidence.document_name,
                        version_label=evidence.version_label,
                        section_label=" / ".join(evidence.section_path),
                        section_path=list(evidence.section_path),
                        locator_kind=evidence.locator_kind,
                        locator_label=evidence.locator_label,
                        content_hash=evidence.content_hash,
                        dense_score=evidence.dense_score,
                        sparse_score=evidence.sparse_score,
                        fused_score=evidence.fused_score,
                        rerank_score=citation.rerank_score,
                        claim_ids=list(bindings.by_marker[citation.marker]),
                        verification_state=AUTHORITY_VERIFICATION_STATE,
                        prompt_contract_version=PROMPT_CONTRACT_VERSION,
                        grounding_policy_id=policy.id,
                        grounding_policy_version=policy.policy_version,
                        verifier_model_id=policy.verifier_model_id,
                        provider_preset_version=policy.provider_preset_version,
                        binding_revision=policy.binding_revision,
                        credential_fingerprint=policy.credential_fingerprint,
                    )
                )
        await session.commit()
        return message
    except Exception:
        await session.rollback()
        raise


async def _persist_conversational_assistant(
    session: AsyncSession,
    context: TenantContext,
    chat: Chat,
    *,
    parent: Message,
    content: str,
    model_id: UUID | None,
    usage: LLMUsage | None,
) -> Message:
    await tenancy_service.get_workspace(
        session,
        context,
        chat.workspace_id,
        "chat.use",
    )
    try:
        message = await build_message(
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
        message.answer_status = None
        message.refusal_reason = None
        await session.commit()
        return message
    except Exception:
        await session.rollback()
        raise


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
    model_name = model.litellm_model_name
    model_id = model.id
    user_message_id = user_message.id
    all_messages = await list_messages(session, chat.id)
    history = [
        (message.role, message.content)
        for message in path_to_root(all_messages, user_message)
    ]
    decision = decide_route(user_message.content, history=history)
    yield route_selected_event(decision.route.value, decision.reason_code)

    if decision.route is QueryRoute.CLARIFY:
        yield token_event(CLARIFY_TEXT)
        message = await _persist_conversational_assistant(
            session,
            context,
            chat,
            parent=user_message,
            content=CLARIFY_TEXT,
            model_id=None,
            usage=None,
        )
        yield citations_event([])
        yield done_event(
            message_id=str(message.id),
            prompt_tokens=0,
            completion_tokens=0,
            no_answer=False,
        )
        return

    if decision.route in {QueryRoute.DIRECT, QueryRoute.CONVERSATION}:
        prompt = (
            build_direct_messages(user_message.content)
            if decision.route is QueryRoute.DIRECT
            else build_conversation_messages(
                history=history,
                user_query=user_message.content,
                budget=settings.chat_context_token_budget,
            )
        )
        await session.rollback()
        direct_parts: list[str] = []
        direct_usage: LLMUsage | None = None
        try:
            async for item in streamer.stream(model=model_name, messages=prompt):
                if isinstance(item, LLMDelta):
                    direct_parts.append(item.text)
                    yield token_event(item.text)
                else:
                    direct_usage = item
        except UpstreamError as exc:
            yield error_event(exc.detail or "LLM gateway error")
            return
        answer = "".join(direct_parts)
        if not answer:
            yield error_event("LLM gateway returned an empty response")
            return
        current_chat, current_parent = await get_message(
            session,
            context,
            user_message_id,
        )
        message = await _persist_conversational_assistant(
            session,
            context,
            current_chat,
            parent=current_parent,
            content=answer,
            model_id=model_id,
            usage=direct_usage,
        )
        yield citations_event([])
        yield done_event(
            message_id=str(message.id),
            prompt_tokens=(
                direct_usage.prompt_tokens if direct_usage is not None else 0
            ),
            completion_tokens=(
                direct_usage.completion_tokens
                if direct_usage is not None
                else 0
            ),
            no_answer=False,
        )
        return

    assert decision.retrieval_query is not None
    yield retrieval_started_event()
    result = await retriever(
        session,
        context,
        chat.workspace_id,
        decision.retrieval_query,
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
            refusal_reason=(
                result.decision.reason_code or "below_threshold"
                if result.decision is not None
                else "below_threshold"
            ),
        )
        yield citations_event([])
        yield done_event(
            message_id=str(message.id),
            prompt_tokens=0,
            completion_tokens=0,
            no_answer=True,
        )
        return

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
    await session.rollback()
    try:
        async for item in streamer.stream(
            model=model_name,
            messages=prompt,
        ):
            if isinstance(item, LLMDelta):
                parts.append(item.text)
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
                by_marker[marker].evidence_span_id
                or (
                    f"{by_marker[marker].document_id}:"
                    f"{by_marker[marker].page}:"
                    f"{by_marker[marker].chunk_index}"
                )
            ),
            page=by_marker[marker].page,
            score=by_marker[marker].score,
            document_version_id=by_marker[marker].document_version_id,
            evidence_span_id=by_marker[marker].evidence_span_id,
            document_name=by_marker[marker].filename,
            version_label=by_marker[marker].version_label,
            section_label=by_marker[marker].section_label,
            section_path=by_marker[marker].section_path,
            locator_kind=by_marker[marker].locator_kind,
            locator_label=by_marker[marker].locator_label,
            content_hash=by_marker[marker].content_hash,
            dense_score=by_marker[marker].dense_score,
            sparse_score=by_marker[marker].sparse_score,
            fused_score=by_marker[marker].fused_score,
            rerank_score=by_marker[marker].rerank_score,
        )
        for marker in markers
    ]
    current_chat, current_parent = await get_message(
        session,
        context,
        user_message_id,
    )
    message = await _persist_assistant(
        session,
        context,
        current_chat,
        parent=current_parent,
        content=answer,
        model_id=model_id,
        usage=usage,
        citations=citation_references,
    )
    persisted_citations = (
        citation_references if message.answer_status != "refused" else []
    )
    if persisted_citations:
        for part in parts:
            yield token_event(part)
    else:
        yield token_event(NO_ANSWER_TEXT)
    yield citations_event(persisted_citations)
    yield done_event(
        message_id=str(message.id),
        prompt_tokens=usage.prompt_tokens if usage is not None else 0,
        completion_tokens=(
            usage.completion_tokens if usage is not None else 0
        ),
        no_answer=not persisted_citations,
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
