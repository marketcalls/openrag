"""Deterministic, bounded metrics for production RAG evaluations."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import log2
from statistics import fmean


@dataclass(frozen=True, slots=True)
class RankMetrics:
    recall: float
    precision: float
    mrr: float
    ndcg: float


@dataclass(frozen=True, slots=True)
class CitationMetrics:
    precision: float
    recall: float
    groundedness: float


@dataclass(frozen=True, slots=True)
class AggregateMetrics:
    case_count: int
    recall: float | None
    precision: float | None
    mrr: float | None
    ndcg: float | None
    citation_precision: float | None
    citation_recall: float | None
    groundedness: float | None
    answer_relevance: float | None
    correct_refusal: float | None


_METRIC_NAMES = (
    "recall",
    "precision",
    "mrr",
    "ndcg",
    "citation_precision",
    "citation_recall",
    "groundedness",
    "answer_relevance",
    "correct_refusal",
)


def _deduplicate(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def rank_metrics(*, retrieved: Sequence[str], relevant: set[str], k: int) -> RankMetrics:
    """Calculate binary relevance metrics at ``k`` with duplicate-safe hits."""

    if k < 1:
        raise ValueError("evaluation_k_invalid")
    ranked = _deduplicate(retrieved[:k])
    hits = [identifier in relevant for identifier in ranked]
    hit_count = sum(hits)
    if not relevant:
        recall = 1.0
        precision = 1.0 if not ranked else 0.0
        ndcg = 1.0
    else:
        recall = hit_count / len(relevant)
        precision = hit_count / k
        dcg = sum(1 / log2(rank + 2) for rank, hit in enumerate(hits) if hit)
        ideal_hits = min(len(relevant), k)
        ideal_dcg = sum(1 / log2(rank + 2) for rank in range(ideal_hits))
        ndcg = dcg / ideal_dcg if ideal_dcg else 0.0
    first_hit = next((rank for rank, hit in enumerate(hits, start=1) if hit), None)
    return RankMetrics(
        recall=min(1.0, recall),
        precision=min(1.0, precision),
        mrr=0.0 if first_hit is None else 1 / first_hit,
        ndcg=min(1.0, ndcg),
    )


def citation_metrics(
    *,
    cited: set[str],
    expected: set[str],
    retrieved: set[str],
) -> CitationMetrics:
    """Score citation correctness, coverage, and retrieval-grounded support."""

    correct = cited & expected
    precision = len(correct) / len(cited) if cited else (1.0 if not expected else 0.0)
    recall = len(correct) / len(expected) if expected else 1.0
    supported = cited & retrieved
    groundedness = len(supported) / len(cited) if cited else 1.0
    return CitationMetrics(
        precision=precision,
        recall=recall,
        groundedness=groundedness,
    )


def refusal_metrics(*, should_refuse: bool, did_refuse: bool) -> float:
    """Return one only when the system's answer/refusal decision is correct."""

    return 1.0 if should_refuse == did_refuse else 0.0


def aggregate_case_metrics(
    cases: Sequence[Mapping[str, float | None]],
) -> AggregateMetrics:
    """Macro-average cases; missing optional metrics receive no hidden weight."""

    values: dict[str, list[float]] = {name: [] for name in _METRIC_NAMES}
    for case in cases:
        unknown = set(case) - set(_METRIC_NAMES)
        if unknown:
            raise ValueError("metric_unknown")
        for name, value in case.items():
            if value is None:
                continue
            if not 0.0 <= value <= 1.0:
                raise ValueError("metric_out_of_bounds")
            values[name].append(value)

    means = {
        name: fmean(metric_values) if metric_values else None
        for name, metric_values in values.items()
    }
    return AggregateMetrics(
        case_count=len(cases),
        recall=means["recall"],
        precision=means["precision"],
        mrr=means["mrr"],
        ndcg=means["ndcg"],
        citation_precision=means["citation_precision"],
        citation_recall=means["citation_recall"],
        groundedness=means["groundedness"],
        answer_relevance=means["answer_relevance"],
        correct_refusal=means["correct_refusal"],
    )
