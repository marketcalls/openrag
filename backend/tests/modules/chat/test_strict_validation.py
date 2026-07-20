from collections.abc import AsyncIterator
from uuid import uuid4

from openrag.modules.chat.events import SourceRef
from openrag.modules.chat.llm import LLMDelta, LLMUsage
from openrag.modules.chat.service import _validate_strict_draft
from openrag.modules.orchestration.answer_validation import (
    AnswerValidation,
    BoundAnswerValidator,
)


class RetryStreamer:
    def __init__(self, parts: list[str]) -> None:
        self.parts = parts
        self.calls = 0

    async def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[LLMDelta | LLMUsage]:
        del model
        self.calls += 1
        assert "failed grounded-answer validation" in messages[1]["content"]
        for part in self.parts:
            yield LLMDelta(part)
        yield LLMUsage(prompt_tokens=7, completion_tokens=2)


def _source() -> SourceRef:
    return SourceRef(
        marker=1,
        document_id=str(uuid4()),
        filename="Policy.pdf",
        page=4,
        chunk_index=1,
        score=0.9,
        snippet="Approved evidence",
        document_version_id=str(uuid4()),
        evidence_span_id=str(uuid4()),
        version_label="Approved 4",
        content_hash="a" * 64,
    )


def _bound(*results: AnswerValidation) -> BoundAnswerValidator:
    pending = list(results)

    async def validate(**_kwargs: object) -> AnswerValidation:
        return pending.pop(0)

    return BoundAnswerValidator(
        policy_id=uuid4(),
        policy_version=3,
        verifier_model_id=uuid4(),
        validate_call=validate,
    )


async def test_strict_validation_passes_without_regeneration() -> None:
    streamer = RetryStreamer(["must not run"])
    passed = AnswerValidation(
        "passed",
        "validated",
        0.96,
        0.9,
        LLMUsage(3, 1),
    )

    result = await _validate_strict_draft(
        answer_validator=_bound(passed),
        streamer=streamer,
        model_name="answer-model",
        prompt=[{"role": "system", "content": "policy"}, {"role": "user", "content": "q"}],
        question="What is required?",
        initial_parts=["Wear PPE [1]."],
        initial_usage=LLMUsage(10, 4),
        sources=[_source()],
        evidence_texts=("Wear PPE.",),
    )

    assert result.answer == "Wear PPE [1]."
    assert result.refusal_reason == "below_threshold"
    assert result.usage == LLMUsage(13, 5)
    assert streamer.calls == 0


async def test_missing_citations_regenerates_once_with_inline_markers() -> None:
    streamer = RetryStreamer(["Corrected answer [1]."])

    result = await _validate_strict_draft(
        answer_validator=None,
        streamer=streamer,
        model_name="answer-model",
        prompt=[{"role": "system", "content": "policy"}, {"role": "user", "content": "q"}],
        question="What is required?",
        initial_parts=["Correct answer without a source marker."],
        initial_usage=LLMUsage(10, 4),
        sources=[_source()],
        evidence_texts=("Corrected answer.",),
    )

    assert result.answer == "Corrected answer [1]."
    assert result.citations[0].marker == 1
    assert result.usage == LLMUsage(17, 6)
    assert streamer.calls == 1


async def test_strict_validation_regenerates_once_and_revalidates() -> None:
    failed = AnswerValidation(
        "failed",
        "unsupported_claims",
        0.4,
        0.9,
        LLMUsage(3, 1),
    )
    passed = AnswerValidation(
        "passed",
        "validated",
        0.97,
        0.9,
        LLMUsage(4, 1),
    )
    streamer = RetryStreamer(["Corrected answer [1]."])

    result = await _validate_strict_draft(
        answer_validator=_bound(failed, passed),
        streamer=streamer,
        model_name="answer-model",
        prompt=[{"role": "system", "content": "policy"}, {"role": "user", "content": "q"}],
        question="What is required?",
        initial_parts=["Unsupported answer [1]."],
        initial_usage=LLMUsage(10, 4),
        sources=[_source()],
        evidence_texts=("Corrected answer.",),
    )

    assert result.answer == "Corrected answer [1]."
    assert result.citations
    assert result.usage == LLMUsage(24, 8)
    assert streamer.calls == 1


async def test_strict_validation_outage_removes_citations_and_refuses() -> None:
    unavailable = AnswerValidation(
        "unavailable",
        "verifier_unavailable",
        None,
        None,
        LLMUsage(2, 0),
    )
    streamer = RetryStreamer(["must not run"])

    result = await _validate_strict_draft(
        answer_validator=_bound(unavailable),
        streamer=streamer,
        model_name="answer-model",
        prompt=[{"role": "system", "content": "policy"}, {"role": "user", "content": "q"}],
        question="What is required?",
        initial_parts=["Private draft [1]."],
        initial_usage=LLMUsage(10, 4),
        sources=[_source()],
        evidence_texts=("Evidence.",),
    )

    assert result.citations == []
    assert result.refusal_reason == "strict_verifier_unavailable"
    assert streamer.calls == 0
