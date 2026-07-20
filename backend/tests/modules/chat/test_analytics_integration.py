from dataclasses import dataclass, field

from openrag.core.errors import UpstreamError
from openrag.modules.artifacts.prompting import AnalyticsEvidence
from openrag.modules.artifacts.schemas import AnalyticsResponseV1
from openrag.modules.chat.events import CitationRef
from openrag.modules.chat.llm import LLMUsage
from openrag.modules.chat.service import _compose_analytics_artifact
from openrag.modules.orchestration.agno_analytics import AnalyticsComposition
from openrag.modules.orchestration.routing import QueryRoute


def _artifact() -> AnalyticsResponseV1:
    return AnalyticsResponseV1.model_validate(
        {
            "schema_version": "analytics.v1",
            "title": "Revenue overview",
            "subtitle": None,
            "kpis": [],
            "blocks": [
                {
                    "kind": "explainer",
                    "title": "Trend",
                    "body_markdown": "Revenue increased [2].",
                    "source_markers": [2],
                }
            ],
            "suggested_followups": [],
        }
    )


def _citation(marker: int) -> CitationRef:
    return CitationRef(
        marker=marker,
        document_id=f"00000000-0000-0000-0000-00000000000{marker}",
        chunk_ref=f"span-{marker}",
        page=marker,
        score=0.9,
    )


@dataclass
class RecordingComposer:
    fail: bool = False
    calls: list[dict[str, object]] = field(default_factory=list)

    async def compose(self, **kwargs: object) -> AnalyticsComposition:
        self.calls.append(kwargs)
        if self.fail:
            raise UpstreamError("analytics composition failed")
        return AnalyticsComposition(
            artifact=_artifact(),
            usage=LLMUsage(5, 2, 7),
        )


async def test_analytics_composition_uses_only_cited_evidence() -> None:
    composer = RecordingComposer()

    result = await _compose_analytics_artifact(
        route=QueryRoute.ANALYTICS,
        composer=composer,
        question="Show the revenue trend",
        answer="Revenue increased [2].",
        citations=[_citation(2)],
        evidence_texts=("Uncited first source", "Cited second source"),
    )

    assert result is not None
    assert result.artifact.title == "Revenue overview"
    assert composer.calls == [
        {
            "question": "Show the revenue trend",
            "answer_markdown": "Revenue increased [2].",
            "evidence": (AnalyticsEvidence(marker=2, text="Cited second source"),),
            "allowed_markers": (2,),
        }
    ]


async def test_analytics_composition_is_gated_and_failure_is_non_fatal() -> None:
    composer = RecordingComposer()
    common = {
        "composer": composer,
        "question": "Show the revenue trend",
        "answer": "Revenue increased [1].",
        "citations": [_citation(1)],
        "evidence_texts": ("Revenue increased",),
    }

    assert await _compose_analytics_artifact(route=QueryRoute.RAG, **common) is None
    assert (
        await _compose_analytics_artifact(
            route=QueryRoute.ANALYTICS,
            **{**common, "composer": None},
        )
        is None
    )
    assert (
        await _compose_analytics_artifact(
            route=QueryRoute.ANALYTICS,
            **{**common, "citations": []},
        )
        is None
    )
    assert composer.calls == []

    composer.fail = True
    assert await _compose_analytics_artifact(route=QueryRoute.ANALYTICS, **common) is None
    assert len(composer.calls) == 1


async def test_composer_cannot_escape_the_grounded_marker_set() -> None:
    composer = RecordingComposer()

    result = await _compose_analytics_artifact(
        route=QueryRoute.ANALYTICS,
        composer=composer,
        question="Show the revenue trend",
        answer="Revenue increased [1].",
        citations=[_citation(1)],
        evidence_texts=("Revenue increased",),
    )

    assert result is None
    assert len(composer.calls) == 1
