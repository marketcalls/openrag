from dataclasses import replace
from uuid import UUID

import pytest

from openrag.modules.retrieval.service import (
    RetrievalResult,
    RetrievedChunk,
    RetrievedEvidence,
    attach_dense_scores,
    query_requires_document_diversity,
    select_final_chunks,
    select_final_evidence,
    select_generation_result,
)


def evidence(
    suffix: int,
    *,
    document_id: UUID | None = None,
    section_path: tuple[str, ...] = ("Emergency", "Evacuation"),
    content_hash: str | None = None,
) -> RetrievedEvidence:
    return RetrievedEvidence(
        document_id=document_id or UUID("81000000-0000-0000-0000-000000000001"),
        document_version_id=UUID("82000000-0000-0000-0000-000000000002"),
        evidence_span_id=UUID(f"83000000-0000-0000-0000-{suffix:012d}"),
        document_name="HSE Manual",
        version_label="Rev 4",
        section_path=section_path,
        locator_kind="page",
        locator_label="17",
        page_number=17,
        chunk_ref=f"span:{suffix}",
        content_hash=content_hash or f"{suffix:064x}",
        text=f"Evacuation evidence {suffix}",
        chunk_index=suffix,
        dense_score=0.91,
        sparse_score=None,
        fused_score=0.03 - (suffix / 10_000),
        rerank_score=None,
    )


def test_retrieved_evidence_contains_complete_citation_snapshot() -> None:
    item = evidence(1)

    assert item.document_id
    assert item.document_version_id
    assert item.evidence_span_id
    assert item.document_name == "HSE Manual"
    assert item.version_label == "Rev 4"
    assert item.section_path == ("Emergency", "Evacuation")
    assert item.page_number == 17
    assert len(item.content_hash) == 64


def test_final_selection_deduplicates_content_and_caps_section_coverage() -> None:
    duplicate_hash = "f" * 64
    items = [
        evidence(1, content_hash=duplicate_hash),
        evidence(2, content_hash=duplicate_hash),
        evidence(3),
        evidence(4),
        evidence(5, section_path=("Emergency", "Fire")),
    ]

    selected = select_final_evidence(
        items,
        top_k=4,
        max_per_document=4,
        max_per_section=2,
    )

    assert [item.evidence_span_id for item in selected] == [
        items[0].evidence_span_id,
        items[2].evidence_span_id,
        items[4].evidence_span_id,
    ]


def test_broad_query_selection_represents_multiple_documents_before_filling() -> None:
    first_document = UUID("81000000-0000-0000-0000-000000000001")
    second_document = UUID("81000000-0000-0000-0000-000000000002")
    third_document = UUID("81000000-0000-0000-0000-000000000003")
    ranked = [
        RetrievedChunk(first_document, 1, 0, "Invoice A header", 0.99),
        RetrievedChunk(first_document, 1, 1, "Invoice A total", 0.98),
        RetrievedChunk(first_document, 1, 2, "Invoice A terms", 0.97),
        RetrievedChunk(second_document, 1, 0, "Invoice B", 0.80),
        RetrievedChunk(third_document, 1, 0, "Invoice C", 0.79),
    ]

    selected = select_final_chunks(
        ranked,
        top_k=4,
        prefer_document_diversity=True,
    )

    assert [item.document_id for item in selected[:3]] == [
        first_document,
        second_document,
        third_document,
    ]
    assert selected[3] == ranked[1]


def test_broad_query_detection_is_bounded_to_collection_requests() -> None:
    assert query_requires_document_diversity("What are the list of invoices?") is True
    assert query_requires_document_diversity("Compare all safety policies") is True
    assert query_requires_document_diversity("What is the total on invoice 104?") is False


def test_generation_selection_uses_citation_metadata_for_collection_scope() -> None:
    unrelated = RetrievalResult(
        chunks=[
            RetrievedChunk(
                UUID("81000000-0000-0000-0000-000000000010"),
                1,
                0,
                "Algorithmic trading operational circular",
                0.9,
            )
        ],
        no_answer=False,
        best_dense_score=0.92,
    )
    invoice_document = UUID("81000000-0000-0000-0000-000000000011")
    invoice_evidence = replace(
        evidence(11, document_id=invoice_document),
        document_name="BellTPO.pdf",
        section_path=("Tax Invoice",),
        text="Due date 29-06-2026; grand total INR 2,000.00",
    )
    invoices = RetrievalResult(
        chunks=[
            RetrievedChunk(
                invoice_document,
                1,
                0,
                invoice_evidence.text,
                0.12,
            )
        ],
        no_answer=False,
        evidence=(invoice_evidence,),
        best_dense_score=0.12,
    )

    selected = select_generation_result(
        "List all invoices and their total amounts",
        [(object(), unrelated), (object(), invoices)],  # type: ignore[list-item]
    )

    assert selected is invoices


def test_authority_evidence_can_prioritize_document_coverage() -> None:
    first_document = UUID("81000000-0000-0000-0000-000000000001")
    second_document = UUID("81000000-0000-0000-0000-000000000002")
    items = [
        evidence(1, document_id=first_document),
        evidence(2, document_id=first_document),
        evidence(3, document_id=second_document),
    ]

    selected = select_final_evidence(
        items,
        top_k=3,
        max_per_document=3,
        max_per_section=3,
        prefer_document_diversity=True,
    )

    assert [item.document_id for item in selected] == [
        first_document,
        second_document,
        first_document,
    ]


@pytest.mark.parametrize("top_k", [0, 33])
def test_final_selection_rejects_unbounded_top_k(top_k: int) -> None:
    with pytest.raises(ValueError, match="top_k"):
        select_final_evidence([evidence(1)], top_k=top_k)


@pytest.mark.parametrize(
    "changes",
    [
        {"document_name": ""},
        {"version_label": ""},
        {"section_path": ()},
        {"page_number": 0},
        {"content_hash": "bad"},
        {"fused_score": float("nan")},
    ],
)
def test_retrieved_evidence_rejects_incomplete_or_unbounded_provenance(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="evidence_"):
        replace(evidence(1), **changes)


def test_dense_scores_are_attached_only_by_exact_evidence_identity() -> None:
    first = evidence(1)
    second = evidence(2)
    scored = attach_dense_scores(
        (first, second),
        {first.evidence_span_id: 0.92},
    )

    assert scored[0].dense_score == 0.92
    assert scored[1].dense_score == second.dense_score
