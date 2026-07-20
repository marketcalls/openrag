import asyncio
import hashlib
from uuid import UUID, uuid4

import pytest
from qdrant_client import models
from sqlalchemy.ext.asyncio import AsyncSession

import openrag.modules.retrieval.service as retrieval_service
from openrag.core.db import naive_utc
from openrag.core.errors import WorkspaceAccessDenied
from openrag.modules.auth.models import User
from openrag.modules.documents.models import Document, DocumentVersion
from openrag.modules.retrieval.authority import AuthorizedEvidence
from openrag.modules.retrieval.client import COLLECTION, get_qdrant
from openrag.modules.retrieval.embeddings import embed_sparse, get_dense_embedder
from openrag.modules.retrieval.service import (
    CitationEvidenceIdentity,
    backfill_citation_evidence,
    delete_document_points,
    delete_document_version_points,
    retrieve,
)
from openrag.modules.tenancy.authorization import AuthorizationSnapshot
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import Organization, Workspace, WorkspaceMember
from openrag.modules.tenancy.permissions import ALL_PERMISSIONS


async def seed_workspace(
    session: AsyncSession,
    org_name: str,
    *,
    role: str = "user",
    member: bool = True,
    min_score: float = 0.0,
) -> tuple[TenantContext, Workspace]:
    organization = Organization(name=org_name)
    session.add(organization)
    await session.flush()
    workspace = Workspace(
        org_id=organization.id,
        name="Workspace",
        min_score=min_score,
    )
    user = User(
        org_id=organization.id,
        email=f"user@{org_name}.com",
        password_hash="x",  # noqa: S106 - inert persisted test value
    )
    session.add_all([workspace, user])
    await session.flush()
    if member:
        session.add(
            WorkspaceMember(
                org_id=organization.id,
                workspace_id=workspace.id,
                user_id=user.id,
            )
        )
    await session.commit()
    permissions = (
        ALL_PERMISSIONS if role == "admin" else frozenset({"document.read", "document.upload"})
    )
    context = TenantContext(
        user_id=user.id,
        org_id=organization.id,
        authorization=AuthorizationSnapshot(
            user_id=user.id,
            org_id=organization.id,
            is_platform_superadmin=False,
            org_permissions=permissions,
            workspace_permissions={},
            workspace_ids=(frozenset({workspace.id}) if member else frozenset()),
        ),
    )
    return context, workspace


async def upsert_texts(
    session: AsyncSession,
    context: TenantContext,
    workspace: Workspace,
    texts: list[str],
    *,
    approved: bool = True,
) -> str:
    document_id = str(uuid4())
    document_uuid = UUID(document_id)
    document = Document(
        id=document_uuid,
        org_id=context.org_id,
        workspace_id=workspace.id,
        name="Retrieval fixture",
        created_by=context.user_id,
    )
    session.add(document)
    await session.flush()
    session.add(
        DocumentVersion(
            id=document_uuid,
            org_id=context.org_id,
            workspace_id=workspace.id,
            document_id=document_uuid,
            sequence=1,
            version_label="Legacy 1",
            version_key="legacy 1",
            content_hash=hashlib.sha256(document_id.encode()).hexdigest(),
            source_filename="fixture.txt",
            source_mime="text/plain",
            source_size_bytes=1,
            source_storage_key=f"fixtures/{document_id}/source",
            source_page_count=1 if approved else None,
            parser_profile_version="legacy/parser-v1",
            ocr_profile_version="legacy/ocr-unknown-v1",
            chunking_profile_version="legacy/chunking-v1",
            embedding_profile_version="legacy/embedding-v1",
            index_profile_version="legacy/index-v1",
            state="approved" if approved else "processing",
            provenance_state="legacy_pending" if approved else "none",
            created_by=context.user_id,
            approved_by=context.user_id if approved else None,
            approved_at=naive_utc() if approved else None,
            decision_at=naive_utc() if approved else None,
        )
    )
    await session.commit()
    dense_vectors = await get_dense_embedder().embed(texts)
    sparse_vectors = await asyncio.to_thread(embed_sparse, texts)
    points = [
        models.PointStruct(
            id=str(uuid4()),
            vector={"dense": dense, "sparse": sparse},
            payload={
                "tenant_id": str(context.org_id),
                "workspace_id": str(workspace.id),
                "document_id": document_id,
                "page": index + 1,
                "chunk_index": index,
                "text": text,
                "doc_type": "text/plain",
                "date": "2026-07-18",
                "acl_groups": [],
            },
        )
        for index, (text, dense, sparse) in enumerate(
            zip(texts, dense_vectors, sparse_vectors, strict=True)
        )
    ]
    await get_qdrant().upsert(COLLECTION, points=points, wait=True)
    return document_id


async def test_retrieve_returns_matching_chunk(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    context, workspace = await seed_workspace(session, "retrieval-a")
    await upsert_texts(
        session,
        context,
        workspace,
        [
            "the flux capacitor requires 1.21 gigawatts",
            "unrelated kumquat farming notes",
        ],
    )

    result = await retrieve(
        session,
        context,
        workspace.id,
        "flux capacitor gigawatts",
        top_k=2,
    )

    assert not result.no_answer
    assert result.chunks[0].text.startswith("the flux capacitor")
    assert result.chunks[0].page == 1
    assert result.chunks[0].chunk_index == 0


async def test_legacy_citation_backfill_rehydrates_exact_approved_chunk(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    context, workspace = await seed_workspace(session, "citation-backfill")
    document_id = await upsert_texts(
        session,
        context,
        workspace,
        ["invoice amount is 590000", "unrelated second chunk"],
    )
    identity = CitationEvidenceIdentity(
        document_id=UUID(document_id),
        document_version_id=UUID(document_id),
        evidence_span_id=None,
        chunk_ref=f"{document_id}:1:0",
        content_hash=None,
    )

    result = await backfill_citation_evidence(
        session,
        context,
        workspace.id,
        [identity],
        top_k=8,
    )

    assert result.no_answer is False
    assert [(item.text, item.score) for item in result.chunks] == [
        ("invoice amount is 590000", 0.0),
    ]


async def test_legacy_citation_backfill_drops_malformed_cross_tenant_and_unapproved_refs(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    context, workspace = await seed_workspace(session, "citation-backfill-safe")
    other_context, other_workspace = await seed_workspace(
        session,
        "citation-backfill-other",
    )
    other_document_id = await upsert_texts(
        session,
        other_context,
        other_workspace,
        ["other tenant secret"],
    )
    unapproved_document_id = await upsert_texts(
        session,
        context,
        workspace,
        ["unapproved draft"],
        approved=False,
    )
    identities = [
        CitationEvidenceIdentity(
            document_id=UUID(other_document_id),
            document_version_id=UUID(other_document_id),
            evidence_span_id=None,
            chunk_ref=f"{other_document_id}:1:0",
            content_hash=None,
        ),
        CitationEvidenceIdentity(
            document_id=UUID(unapproved_document_id),
            document_version_id=UUID(unapproved_document_id),
            evidence_span_id=None,
            chunk_ref=f"{unapproved_document_id}:1:0",
            content_hash=None,
        ),
        CitationEvidenceIdentity(
            document_id=UUID(unapproved_document_id),
            document_version_id=UUID(unapproved_document_id),
            evidence_span_id=None,
            chunk_ref="malformed",
            content_hash=None,
        ),
    ]

    result = await backfill_citation_evidence(
        session,
        context,
        workspace.id,
        identities,
    )

    assert result.no_answer is True
    assert result.chunks == []


async def test_citation_backfill_is_bounded_before_external_reads(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    context, workspace = await seed_workspace(session, "citation-backfill-bound")
    identity = CitationEvidenceIdentity(
        document_id=uuid4(),
        document_version_id=uuid4(),
        evidence_span_id=None,
        chunk_ref=f"{uuid4()}:1:0",
        content_hash=None,
    )

    with pytest.raises(ValueError, match="citation_identity_limit_exceeded"):
        await backfill_citation_evidence(
            session,
            context,
            workspace.id,
            [identity] * 33,
        )


async def test_authority_citation_backfill_revalidates_identity_and_clears_similarity(
    session: AsyncSession,
    qdrant_collection: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, workspace = await seed_workspace(session, "citation-backfill-authority")
    document_id = uuid4()
    version_id = uuid4()
    span_id = uuid4()
    content_hash = "a" * 64
    identity = CitationEvidenceIdentity(
        document_id=document_id,
        document_version_id=version_id,
        evidence_span_id=span_id,
        chunk_ref=str(span_id),
        content_hash=content_hash,
    )

    async def revalidate(*args: object, **kwargs: object) -> list[AuthorizedEvidence]:
        candidates = args[3]
        assert len(candidates) == 1  # type: ignore[arg-type]
        assert candidates[0].evidence_span_id == span_id  # type: ignore[index]
        assert candidates[0].content_hash == content_hash  # type: ignore[index]
        return [
            AuthorizedEvidence(
                document_id=document_id,
                document_version_id=version_id,
                evidence_span_id=span_id,
                document_name="Invoice.pdf",
                version_label="Rev 2",
                section_path=("Tax Details",),
                locator_kind="page",
                locator_label="1",
                page_number=1,
                chunk_ref=str(span_id),
                content_hash=content_hash,
                text="IGST is 90000",
                chunk_index=4,
                dense_score=0.92,
                sparse_score=0.4,
                fused_score=0.8,
            )
        ]

    monkeypatch.setattr(retrieval_service, "revalidate_candidates", revalidate)

    result = await backfill_citation_evidence(
        session,
        context,
        workspace.id,
        [identity],
    )

    assert result.no_answer is False
    assert len(result.evidence) == 1
    assert result.evidence[0].evidence_span_id == span_id
    assert result.evidence[0].dense_score is None
    assert result.evidence[0].sparse_score is None
    assert result.evidence[0].fused_score == 0.0
    assert result.chunks[0].text == "IGST is 90000"


async def test_min_score_triggers_no_answer_with_nearest_chunk(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    context, workspace = await seed_workspace(
        session,
        "retrieval-b",
        min_score=0.99,
    )
    await upsert_texts(session, context, workspace, ["vaguely related invoice text"])

    result = await retrieve(
        session,
        context,
        workspace.id,
        "completely different query terms",
    )

    assert result.no_answer
    assert result.chunks


async def test_empty_workspace_is_no_answer(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    context, workspace = await seed_workspace(session, "retrieval-c")

    result = await retrieve(session, context, workspace.id, "anything")

    assert result.no_answer
    assert result.chunks == []


async def test_non_member_is_denied(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    context, workspace = await seed_workspace(
        session,
        "retrieval-d",
        member=False,
    )

    with pytest.raises(WorkspaceAccessDenied):
        await retrieve(session, context, workspace.id, "anything")


async def test_admin_without_membership_is_allowed(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    context, workspace = await seed_workspace(
        session,
        "retrieval-e",
        role="admin",
        member=False,
    )

    result = await retrieve(session, context, workspace.id, "anything")

    assert result.chunks == []


async def test_delete_document_points(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    context, workspace = await seed_workspace(session, "retrieval-f")
    document_id = await upsert_texts(session, context, workspace, ["target text to delete"])

    await delete_document_points(context.org_id, UUID(document_id))
    result = await retrieve(
        session,
        context,
        workspace.id,
        "target text to delete",
    )

    assert result.chunks == []


async def test_nonapproved_orphan_qdrant_points_are_never_served(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    context, workspace = await seed_workspace(session, "retrieval-orphan")
    await upsert_texts(
        session,
        context,
        workspace,
        ["orphan vector must not be served"],
        approved=False,
    )

    result = await retrieve(
        session,
        context,
        workspace.id,
        "orphan vector must not be served",
    )

    assert result.no_answer
    assert result.chunks == []


async def test_delete_document_version_points_preserves_siblings_and_other_tenants(
    session: AsyncSession,
    qdrant_collection: None,
) -> None:
    context, workspace = await seed_workspace(session, "retrieval-version-delete")
    other_context, other_workspace = await seed_workspace(session, "retrieval-version-delete-other")
    target_version_id = uuid4()
    sibling_version_id = uuid4()
    point_ids = {
        "target": uuid4(),
        "sibling": uuid4(),
        "other_tenant": uuid4(),
    }
    texts = ["target", "sibling", "other tenant"]
    dense_vectors = await get_dense_embedder().embed(texts)
    sparse_vectors = await asyncio.to_thread(embed_sparse, texts)
    payloads = (
        {
            "tenant_id": str(context.org_id),
            "workspace_id": str(workspace.id),
            "document_id": str(uuid4()),
            "document_version_id": str(target_version_id),
        },
        {
            "tenant_id": str(context.org_id),
            "workspace_id": str(workspace.id),
            "document_id": str(uuid4()),
            "document_version_id": str(sibling_version_id),
        },
        {
            "tenant_id": str(other_context.org_id),
            "workspace_id": str(other_workspace.id),
            "document_id": str(uuid4()),
            "document_version_id": str(target_version_id),
        },
    )
    await get_qdrant().upsert(
        COLLECTION,
        points=[
            models.PointStruct(
                id=str(point_id),
                vector={"dense": dense, "sparse": sparse},
                payload={**payload, "text": text, "page": 1, "chunk_index": 0},
            )
            for point_id, text, dense, sparse, payload in zip(
                point_ids.values(),
                texts,
                dense_vectors,
                sparse_vectors,
                payloads,
                strict=True,
            )
        ],
        wait=True,
    )

    await delete_document_version_points(context.org_id, target_version_id)

    remaining, _ = await get_qdrant().scroll(
        COLLECTION,
        limit=10,
        with_payload=False,
        with_vectors=False,
    )
    assert {str(point.id) for point in remaining} == {
        str(point_ids["sibling"]),
        str(point_ids["other_tenant"]),
    }
