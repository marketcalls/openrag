from collections.abc import AsyncIterator
from uuid import UUID

from qdrant_client import models

from openrag.modules.chat.llm import LLMDelta, LLMUsage
from openrag.modules.documents.enrichment_points import EnrichmentEvidence
from openrag.modules.documents.enrichment_runtime import (
    PreparedEnrichmentBatch,
    execute_prepared_enrichment,
)


class FakeStreamer:
    async def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[LLMDelta | LLMUsage]:
        del model, messages
        yield LLMDelta(
            '{"summary":"PPE rules.","keywords":["ppe"],'
            '"hypothetical_questions":["What PPE is required?"]}'
        )
        yield LLMUsage(prompt_tokens=20, completion_tokens=8)


class FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _text in texts]


class FakeWriter:
    def __init__(self) -> None:
        self.collection: str | None = None
        self.points: list[object] = []
        self.deleted: models.FilterSelector | None = None

    async def delete(
        self,
        collection_name: str,
        *,
        points_selector: models.FilterSelector,
        wait: bool,
    ) -> object:
        assert collection_name == "openrag-authority-test"
        assert wait is True
        self.deleted = points_selector
        return object()

    async def upsert(
        self,
        collection_name: str,
        *,
        points: list[object],
        wait: bool,
    ) -> object:
        assert wait is True
        self.collection = collection_name
        self.points = points
        return object()


async def test_prepared_batch_enriches_embeds_and_upserts_without_sql() -> None:
    evidence = EnrichmentEvidence(
        org_id=UUID(int=1),
        workspace_id=UUID(int=2),
        document_id=UUID(int=3),
        document_version_id=UUID(int=4),
        evidence_span_id=UUID(int=5),
        projection_revision=1,
        page_number=1,
        ordinal=0,
        document_name="Manual",
        version_label="v1",
        revision_date=None,
        section_path=("Safety",),
        locator_kind="page",
        locator_label="1",
        content_hash="a" * 64,
        text="Wear PPE.",
        source_mime="application/pdf",
    )
    writer = FakeWriter()
    result = await execute_prepared_enrichment(
        PreparedEnrichmentBatch(
            model_name="utility",
            streamer=FakeStreamer(),
            dense_embedder=FakeEmbedder(),
            collection="openrag-authority-test",
            evidence=(evidence,),
        ),
        writer,
    )

    assert result.generated_evidence == 1
    assert result.invalid_evidence == 0
    assert result.prompt_tokens == 20
    assert result.completion_tokens == 8
    assert result.point_count == 1
    assert writer.collection == "openrag-authority-test"
    assert len(writer.points) == 1
    assert writer.deleted is not None
    must = writer.deleted.filter.must
    assert isinstance(must, list)
    field_keys = {
        condition.key
        for condition in must
        if isinstance(condition, models.FieldCondition)
    }
    assert field_keys == {
        "kind",
        "evidence_span_id",
    }
