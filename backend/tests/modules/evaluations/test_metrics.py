import pytest

from openrag.modules.evaluations.metrics import (
    aggregate_case_metrics,
    citation_metrics,
    rank_metrics,
    refusal_metrics,
)


def test_rank_metrics_match_known_order() -> None:
    result = rank_metrics(retrieved=["b", "a", "c"], relevant={"a", "c"}, k=3)

    assert result.recall == 1.0
    assert result.precision == pytest.approx(2 / 3)
    assert result.mrr == 0.5
    assert result.ndcg == pytest.approx(0.6934264036)


def test_rank_metrics_are_bounded_for_empty_or_duplicate_results() -> None:
    empty = rank_metrics(retrieved=[], relevant=set(), k=5)
    duplicate = rank_metrics(retrieved=["a", "a", "b"], relevant={"a", "b"}, k=3)

    assert empty.recall == 1.0
    assert empty.precision == 1.0
    assert empty.mrr == 0.0
    assert empty.ndcg == 1.0
    assert duplicate.recall == 1.0
    assert duplicate.precision == pytest.approx(2 / 3)
    assert duplicate.mrr == 1.0
    assert 0 <= duplicate.ndcg <= 1


def test_citation_metrics_measure_supported_and_complete_evidence() -> None:
    result = citation_metrics(
        cited={"span-a", "span-x"},
        expected={"span-a", "span-b"},
        retrieved={"span-a", "span-b", "span-c"},
    )

    assert result.precision == 0.5
    assert result.recall == 0.5
    assert result.groundedness == 0.5


@pytest.mark.parametrize(
    ("should_refuse", "did_refuse", "expected"),
    [(True, True, 1.0), (True, False, 0.0), (False, False, 1.0), (False, True, 0.0)],
)
def test_refusal_correctness_is_explicit(
    should_refuse: bool,
    did_refuse: bool,
    expected: float,
) -> None:
    assert refusal_metrics(should_refuse=should_refuse, did_refuse=did_refuse) == expected


def test_aggregate_case_metrics_uses_macro_average_without_hidden_weights() -> None:
    result = aggregate_case_metrics(
        [
            {
                "recall": 1.0,
                "precision": 0.5,
                "mrr": 1.0,
                "ndcg": 0.8,
                "citation_precision": 0.5,
                "citation_recall": 0.25,
                "groundedness": 0.75,
                "answer_relevance": 0.9,
                "correct_refusal": 1.0,
            },
            {
                "recall": 0.0,
                "precision": 0.0,
                "mrr": 0.0,
                "ndcg": 0.0,
                "citation_precision": 1.0,
                "citation_recall": 1.0,
                "groundedness": 1.0,
                "answer_relevance": None,
                "correct_refusal": 1.0,
            },
        ]
    )

    assert result.case_count == 2
    assert result.recall == 0.5
    assert result.citation_precision == 0.75
    assert result.answer_relevance == 0.9
    assert result.correct_refusal == 1.0


def test_aggregate_rejects_unbounded_metric_values() -> None:
    with pytest.raises(ValueError, match="metric_out_of_bounds"):
        aggregate_case_metrics([{"recall": 1.2}])
