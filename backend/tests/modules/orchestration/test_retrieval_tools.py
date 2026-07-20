from collections.abc import Mapping
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from openrag.modules.orchestration.agent_loop import AgentToolCall, MetadataScalar
from openrag.modules.orchestration.retrieval_tools import (
    AuthorizedToolEvidence,
    RetrievalToolExecutor,
    _metadata_document_query,
    merge_authoritative_evidence,
)
from openrag.modules.retrieval.service import RetrievedEvidence


def _evidence(
    org_id: UUID,
    workspace_id: UUID,
    *,
    text: str = "Policy text",
) -> AuthorizedToolEvidence:
    return AuthorizedToolEvidence(
        org_id=org_id,
        workspace_id=workspace_id,
        evidence=RetrievedEvidence(
            document_id=uuid4(),
            document_version_id=uuid4(),
            evidence_span_id=uuid4(),
            document_name="HSE Policy.pdf",
            version_label="v3",
            section_path=("Emergency", "Response"),
            locator_kind="page",
            locator_label="7",
            page_number=7,
            chunk_ref="chunk-7",
            content_hash="a" * 64,
            text=text,
            chunk_index=6,
            dense_score=0.91,
            sparse_score=0.72,
            fused_score=0.88,
        ),
    )


class _Backend:
    def __init__(self, rows: tuple[AuthorizedToolEvidence, ...]) -> None:
        self.rows = rows
        self.searches: list[
            tuple[str, Mapping[str, MetadataScalar] | None]
        ] = []
        self.documents: list[UUID] = []

    async def search(
        self,
        query: str,
        metadata: Mapping[str, MetadataScalar] | None,
    ) -> tuple[AuthorizedToolEvidence, ...]:
        self.searches.append((query, metadata))
        return self.rows

    async def get_document(self, document_id: UUID) -> tuple[AuthorizedToolEvidence, ...]:
        self.documents.append(document_id)
        return self.rows


async def test_executor_dispatches_metadata_search_and_retains_citable_evidence() -> None:
    org_id = uuid4()
    workspace_id = uuid4()
    row = _evidence(org_id, workspace_id)
    backend = _Backend((row, row))
    executor = RetrievalToolExecutor(
        backend,
        org_id=org_id,
        workspace_id=workspace_id,
    )

    result = await executor(
        AgentToolCall(
            name="search_by_metadata",
            query="emergency response",
            metadata={"department": "HSE"},
        )
    )

    assert backend.searches == [("emergency response", {"department": "HSE"})]
    assert result.provenance_refs == (str(row.evidence.evidence_span_id),)
    assert "HSE Policy.pdf" in result.text
    assert "version=v3" in result.text
    assert "section=Emergency / Response" in result.text
    assert "page=7" in result.text
    assert executor.collected_evidence == (row.evidence,)


async def test_executor_reads_an_exact_document_through_the_tenant_bound_backend() -> None:
    org_id = uuid4()
    workspace_id = uuid4()
    document_id = uuid4()
    backend = _Backend((_evidence(org_id, workspace_id),))
    executor = RetrievalToolExecutor(
        backend,
        org_id=org_id,
        workspace_id=workspace_id,
    )

    await executor(AgentToolCall(name="get_document", document_id=str(document_id)))

    assert backend.documents == [document_id]


async def test_executor_fails_closed_on_scope_mismatch_or_empty_evidence() -> None:
    org_id = uuid4()
    workspace_id = uuid4()
    wrong_scope = _evidence(uuid4(), workspace_id)
    executor = RetrievalToolExecutor(
        _Backend((wrong_scope,)),
        org_id=org_id,
        workspace_id=workspace_id,
    )
    with pytest.raises(ValueError, match="tool_evidence_scope_mismatch"):
        await executor(AgentToolCall(name="search", query="policy"))

    empty = RetrievalToolExecutor(
        _Backend(()),
        org_id=org_id,
        workspace_id=workspace_id,
    )
    result = await empty(AgentToolCall(name="search", query="missing"))
    assert result.text == "No authorized evidence found."
    assert result.provenance_refs == ()


def test_metadata_search_accepts_only_indexed_enterprise_fields() -> None:
    with pytest.raises(ValueError, match="tool_metadata_key_not_allowed"):
        AgentToolCall(
            name="search_by_metadata",
            query="policy",
            metadata={"acl_policy": "workspace"},
        )


def test_metadata_query_is_tenant_pinned_current_and_bounded() -> None:
    statement = _metadata_document_query(
        org_id=uuid4(),
        workspace_id=uuid4(),
        metadata={
            "department": "HSE",
            "version_label": "v3",
            "revision_date_from": "2026-01-01",
            "section": "Emergency",
        },
    )
    compiled = statement.compile(dialect=postgresql.dialect())  # type: ignore[no-untyped-call]
    sql = str(compiled).upper()

    assert "DOCUMENTS.ORG_ID" in sql
    assert "DOCUMENTS.WORKSPACE_ID" in sql
    assert "DOCUMENT_VERSIONS.STATE =" in sql
    assert "DOCUMENT_VERSIONS.SUPERSEDED_BY_ID IS NULL" in sql
    assert "DOCUMENTS.DEPARTMENT =" in sql
    assert "DOCUMENT_VERSIONS.VERSION_LABEL =" in sql
    assert "DOCUMENT_EVIDENCE_SPANS.SECTION_PATH" in sql
    assert "HSE" in compiled.params.values()
    assert "v3" in compiled.params.values()
    assert "approved" in compiled.params.values()
    assert 1_001 in compiled.params.values()


def test_metadata_query_rejects_invalid_revision_dates() -> None:
    with pytest.raises(ValueError, match="revision_date_from_invalid"):
        _metadata_document_query(
            org_id=uuid4(),
            workspace_id=uuid4(),
            metadata={"revision_date_from": "not-a-date"},
        )


def test_merge_deduplicates_reranks_and_rechecks_sufficiency() -> None:
    org_id = uuid4()
    workspace_id = uuid4()
    first = _evidence(org_id, workspace_id).evidence
    stronger_duplicate = replace(
        first,
        dense_score=0.96,
        fused_score=0.94,
        text="Stronger matching policy text",
    )
    second = replace(
        _evidence(org_id, workspace_id, text="Second document").evidence,
        content_hash="b" * 64,
    )

    result = merge_authoritative_evidence(
        "What is the emergency policy?",
        (first, stronger_duplicate, second),
        top_k=8,
        min_score=0.8,
    )

    assert result.no_answer is False
    assert len(result.evidence) == 2
    assert result.evidence[0].text == "Stronger matching policy text"
    assert result.decision is not None
    assert result.decision.best_dense_score == 0.96


def test_merge_refuses_when_agent_evidence_remains_below_threshold() -> None:
    row = _evidence(uuid4(), uuid4()).evidence

    result = merge_authoritative_evidence(
        "What is the emergency policy?",
        (row,),
        top_k=8,
        min_score=0.99,
    )

    assert result.no_answer is True
    assert result.decision is not None
    assert result.decision.reason_code == "below_threshold"
