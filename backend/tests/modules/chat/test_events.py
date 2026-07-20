import json
from typing import cast
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.chat.events import (
    CitationRef,
    SourceRef,
    citations_event,
    done_event,
    error_event,
    retrieval_started_event,
    route_selected_event,
    sources_event,
    token_event,
)
from openrag.modules.chat.service import _source_refs
from openrag.modules.retrieval.service import RetrievalResult, RetrievedEvidence
from openrag.modules.tenancy.context import TenantContext


def test_encode_frame_format() -> None:
    frame = token_event("Hel\nlo").encode()

    assert frame.startswith("event: token\ndata: ")
    assert frame.endswith("\n\n")
    payload = frame.split("data: ", 1)[1].rstrip("\n")
    assert json.loads(payload) == {"delta": "Hel\nlo"}


def test_all_event_names_and_payloads() -> None:
    source = SourceRef(
        marker=1,
        document_id="document-1",
        filename="report.pdf",
        page=2,
        chunk_index=0,
        score=0.9,
        snippet="text",
        document_version_id="version-1",
        evidence_span_id="span-1",
        version_label="Rev 4",
        section_label="Safety / PPE",
        section_path=["Safety", "PPE"],
        locator_kind="page",
        locator_label="2",
        content_hash="a" * 64,
        dense_score=0.91,
        sparse_score=0.72,
        fused_score=0.9,
    )
    citation = CitationRef(
        marker=1,
        document_id="document-1",
        chunk_ref="document-1:2:0",
        page=2,
        score=0.9,
        document_version_id="version-1",
        evidence_span_id="span-1",
        document_name="report.pdf",
        version_label="Rev 4",
        section_label="Safety / PPE",
        section_path=["Safety", "PPE"],
        locator_kind="page",
        locator_label="2",
        content_hash="a" * 64,
        dense_score=0.91,
        sparse_score=0.72,
        fused_score=0.9,
    )

    assert retrieval_started_event().event == "retrieval_started"
    assert route_selected_event("direct", "safe_greeting").data == {
        "route": "direct",
        "reason_code": "safe_greeting",
    }
    assert sources_event([source]).data == {
        "sources": [
            {
                "marker": 1,
                "document_id": "document-1",
                "filename": "report.pdf",
                "page": 2,
                "chunk_index": 0,
                "score": 0.9,
                "snippet": "text",
                "document_version_id": "version-1",
                "evidence_span_id": "span-1",
                "version_label": "Rev 4",
                "section_label": "Safety / PPE",
                "section_path": ["Safety", "PPE"],
                "locator_kind": "page",
                "locator_label": "2",
                "content_hash": "a" * 64,
                "dense_score": 0.91,
                "sparse_score": 0.72,
                "fused_score": 0.9,
                "rerank_score": None,
            }
        ]
    }
    assert citations_event([citation]).data == {
        "citations": [
            {
                "marker": 1,
                "document_id": "document-1",
                "chunk_ref": "document-1:2:0",
                "page": 2,
                "score": 0.9,
                "document_version_id": "version-1",
                "evidence_span_id": "span-1",
                "document_name": "report.pdf",
                "version_label": "Rev 4",
                "section_label": "Safety / PPE",
                "section_path": ["Safety", "PPE"],
                "locator_kind": "page",
                "locator_label": "2",
                "content_hash": "a" * 64,
                "dense_score": 0.91,
                "sparse_score": 0.72,
                "fused_score": 0.9,
                "rerank_score": None,
            }
        ]
    }
    done = done_event(
        message_id="message-1",
        prompt_tokens=10,
        completion_tokens=2,
        no_answer=False,
    )
    assert done.event == "done"
    assert done.data == {
        "message_id": "message-1",
        "prompt_tokens": 10,
        "completion_tokens": 2,
        "no_answer": False,
    }
    assert error_event("boom").data == {"detail": "boom"}


async def test_authority_sources_use_the_immutable_evidence_snapshot() -> None:
    evidence = RetrievedEvidence(
        document_id=uuid4(),
        document_version_id=uuid4(),
        evidence_span_id=uuid4(),
        document_name="HSE Manual.pdf",
        version_label="Approved 7",
        section_path=("PPE", "Inspection"),
        locator_kind="page",
        locator_label="19",
        page_number=19,
        chunk_ref="span:19",
        content_hash="b" * 64,
        text="Inspect PPE before every shift.",
        chunk_index=3,
        dense_score=0.94,
        sparse_score=0.71,
        fused_score=0.89,
    )

    sources = await _source_refs(
        cast(AsyncSession, object()),
        cast(TenantContext, object()),
        RetrievalResult(chunks=[], no_answer=False, evidence=(evidence,)),
    )

    assert len(sources) == 1
    assert sources[0] == SourceRef(
        marker=1,
        document_id=str(evidence.document_id),
        filename="HSE Manual.pdf",
        page=19,
        chunk_index=3,
        score=0.89,
        snippet="Inspect PPE before every shift.",
        document_version_id=str(evidence.document_version_id),
        evidence_span_id=str(evidence.evidence_span_id),
        version_label="Approved 7",
        section_label="PPE / Inspection",
        section_path=["PPE", "Inspection"],
        locator_kind="page",
        locator_label="19",
        content_hash="b" * 64,
        dense_score=0.94,
        sparse_score=0.71,
        fused_score=0.89,
    )
