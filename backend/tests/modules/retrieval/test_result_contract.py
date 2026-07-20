from dataclasses import replace
from uuid import UUID

import pytest

from openrag.modules.retrieval.service import (
    RetrievedEvidence,
    select_final_evidence,
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
