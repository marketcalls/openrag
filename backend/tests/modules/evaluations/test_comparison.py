from pathlib import Path

from openrag.modules.evaluations.comparison import (
    StrategyObservation,
    compare_observations,
    load_benchmark,
    score_observation,
    summarize,
)


def test_versioned_agentic_benchmark_has_required_size_and_coverage() -> None:
    dataset = load_benchmark(
        Path(__file__).parents[3] / "benchmarks" / "agentic_rag_v1.json"
    )

    assert len(dataset.cases) >= 50
    assert len({case.category for case in dataset.cases}) == 9
    assert sum(case.requires_ocr for case in dataset.cases) >= 10
    assert sum(case.requires_table for case in dataset.cases) >= 10
    assert sum(case.should_refuse for case in dataset.cases) >= 5
    assert sum(case.minimum_searches >= 2 for case in dataset.cases) >= 10


def test_comparison_metrics_measure_retrieval_citations_refusal_searches_and_cost() -> None:
    dataset = load_benchmark(
        Path(__file__).parents[3] / "benchmarks" / "agentic_rag_v1.json"
    )
    case = dataset.cases[0]
    score = score_observation(
        case,
        StrategyObservation(
            case_id=case.id,
            retrieved_documents=case.expected_documents,
            cited_pages=case.expected_pages,
            did_refuse=case.should_refuse,
            search_count=case.minimum_searches,
            latency_ms=125,
            prompt_tokens=100,
            completion_tokens=25,
        ),
    )
    summary = summarize((score,))

    assert summary.document_recall == 1
    assert summary.page_recall == 1
    assert summary.correct_refusal_rate == 1
    assert summary.search_requirement_rate == 1
    assert summary.mean_latency_ms == 125
    assert summary.mean_tokens == 125


def test_comparison_requires_complete_like_for_like_runs() -> None:
    dataset = load_benchmark(
        Path(__file__).parents[3] / "benchmarks" / "agentic_rag_v1.json"
    )
    simple = tuple(
        StrategyObservation(
            case_id=case.id,
            retrieved_documents=(),
            cited_pages=(),
            did_refuse=True,
            search_count=1,
            latency_ms=100,
        )
        for case in dataset.cases
    )
    agentic = tuple(
        StrategyObservation(
            case_id=case.id,
            retrieved_documents=case.expected_documents,
            cited_pages=case.expected_pages,
            did_refuse=case.should_refuse,
            search_count=max(1, case.minimum_searches),
            latency_ms=150,
            prompt_tokens=20,
            completion_tokens=5,
        )
        for case in dataset.cases
    )

    report = compare_observations(dataset, simple=simple, agentic=agentic)

    assert report.agentic.document_recall == 1
    assert report.agentic.page_recall == 1
    assert report.agentic.correct_refusal_rate == 1
    assert report.latency_delta_ms == 50
    assert report.token_delta == 25
