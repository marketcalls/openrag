from collections.abc import AsyncIterator

from openrag.core.errors import UpstreamError
from openrag.modules.chat.llm import LLMDelta, LLMUsage
from openrag.modules.orchestration.answer_validation import StrictAnswerValidator


class FakeStreamer:
    def __init__(self, parts: list[str] | None = None, *, fail: bool = False) -> None:
        self.parts = parts or []
        self.fail = fail
        self.calls: list[list[dict[str, str]]] = []

    async def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[LLMDelta | LLMUsage]:
        del model
        self.calls.append(messages)
        if self.fail:
            raise UpstreamError("private provider failure")
        for part in self.parts:
            yield LLMDelta(part)
        yield LLMUsage(prompt_tokens=11, completion_tokens=3)


async def test_verifier_passes_only_when_every_threshold_is_met() -> None:
    streamer = FakeStreamer(
        [
            '{"grounded":true,"grounding_score":0.94,',
            '"completeness_score":0.91}',
        ]
    )
    validator = StrictAnswerValidator(
        streamer,
        model_name="verifier-model",
        entailment_threshold=0.9,
        completeness_threshold=0.8,
    )

    result = await validator.validate(
        question="What PPE is required?",
        answer="Wear a helmet [1].",
        evidence=("The approved PPE policy requires a helmet.",),
    )

    assert result.status == "passed"
    assert result.grounding_score == 0.94
    assert result.completeness_score == 0.91
    assert result.usage == LLMUsage(prompt_tokens=11, completion_tokens=3)
    prompt = streamer.calls[0]
    assert "untrusted data" in prompt[0]["content"]
    assert "What PPE is required?" in prompt[1]["content"]


async def test_verifier_rejects_a_model_claiming_grounded_below_threshold() -> None:
    validator = StrictAnswerValidator(
        FakeStreamer(
            ['{"grounded":true,"grounding_score":0.89,"completeness_score":0.99}']
        ),
        model_name="verifier-model",
        entailment_threshold=0.9,
    )

    result = await validator.validate(
        question="Question",
        answer="Answer [1].",
        evidence=("Evidence",),
    )

    assert result.status == "failed"
    assert result.reason_code == "below_entailment_threshold"


async def test_verifier_fails_closed_on_malformed_or_extra_output() -> None:
    validator = StrictAnswerValidator(
        FakeStreamer(
            [
                '{"grounded":true,"grounding_score":1,',
                '"completeness_score":1,"reasoning":"secret"}',
            ]
        ),
        model_name="verifier-model",
        entailment_threshold=0.9,
    )

    result = await validator.validate(
        question="Question",
        answer="Answer [1].",
        evidence=("Evidence",),
    )

    assert result.status == "unavailable"
    assert result.reason_code == "verifier_output_invalid"
    assert "secret" not in repr(result)


async def test_verifier_fails_closed_without_leaking_provider_details() -> None:
    validator = StrictAnswerValidator(
        FakeStreamer(fail=True),
        model_name="verifier-model",
        entailment_threshold=0.9,
    )

    result = await validator.validate(
        question="Question",
        answer="Answer [1].",
        evidence=("Evidence",),
    )

    assert result.status == "unavailable"
    assert result.reason_code == "verifier_unavailable"
    assert "private provider failure" not in repr(result)


async def test_verifier_rejects_unbounded_inputs_before_provider_work() -> None:
    streamer = FakeStreamer(
        ['{"grounded":true,"grounding_score":1,"completeness_score":1}']
    )
    validator = StrictAnswerValidator(
        streamer,
        model_name="verifier-model",
        entailment_threshold=0.9,
    )

    result = await validator.validate(
        question="q" * 2_001,
        answer="Answer",
        evidence=("Evidence",),
    )

    assert result.status == "unavailable"
    assert result.reason_code == "verifier_input_invalid"
    assert streamer.calls == []
