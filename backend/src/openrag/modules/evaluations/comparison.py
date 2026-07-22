"""Versioned Simple-RAG versus Agentic-RAG benchmark contracts."""

import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

BenchmarkCategory = Literal[
    "direct_factual",
    "multi_document",
    "multiple_searches",
    "ambiguous",
    "unanswerable",
    "follow_up",
    "scanned_ocr",
    "table",
    "digital_and_ocr",
]


class BenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(pattern=r"^AR-[0-9]{3}$")
    category: BenchmarkCategory
    question: str = Field(min_length=3, max_length=2_000)
    expected_documents: tuple[str, ...] = Field(max_length=8)
    expected_pages: tuple[int, ...] = Field(max_length=16)
    should_refuse: bool
    requires_ocr: bool
    requires_table: bool
    minimum_searches: int = Field(ge=0, le=4)


class BenchmarkDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["openrag.agentic-benchmark.v1"]
    name: str = Field(min_length=1, max_length=200)
    cases: tuple[BenchmarkCase, ...] = Field(min_length=50, max_length=500)

    def validate_coverage(self) -> None:
        ids = [case.id for case in self.cases]
        if len(set(ids)) != len(ids):
            raise ValueError("benchmark_case_ids_not_unique")
        counts = Counter(case.category for case in self.cases)
        missing = set(BenchmarkCategory.__args__) - set(counts)  # type: ignore[attr-defined]
        if missing:
            raise ValueError("benchmark_category_missing")
        if any(counts[category] < 5 for category in counts):
            raise ValueError("benchmark_category_underrepresented")


@dataclass(frozen=True, slots=True)
class StrategyObservation:
    case_id: str
    retrieved_documents: tuple[str, ...]
    cited_pages: tuple[int, ...]
    did_refuse: bool
    search_count: int
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(frozen=True, slots=True)
class CaseScore:
    case_id: str
    document_recall: float
    page_recall: float
    refusal_correct: bool
    search_requirement_met: bool
    latency_ms: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class StrategySummary:
    cases: int
    document_recall: float
    page_recall: float
    correct_refusal_rate: float
    search_requirement_rate: float
    mean_latency_ms: float
    mean_tokens: float


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    simple: StrategySummary
    agentic: StrategySummary
    document_recall_delta: float
    page_recall_delta: float
    refusal_rate_delta: float
    latency_delta_ms: float
    token_delta: float


def load_benchmark(path: Path) -> BenchmarkDataset:
    raw = path.read_bytes()
    if not raw or len(raw) > 2_000_000:
        raise ValueError("benchmark_file_invalid")
    dataset = BenchmarkDataset.model_validate_json(raw)
    dataset.validate_coverage()
    return dataset


def _recall(expected: tuple[object, ...], actual: tuple[object, ...]) -> float:
    if not expected:
        return 1.0
    return len(set(expected) & set(actual)) / len(set(expected))


def score_observation(case: BenchmarkCase, value: StrategyObservation) -> CaseScore:
    if value.case_id != case.id or value.search_count < 0 or value.latency_ms < 0:
        raise ValueError("benchmark_observation_invalid")
    return CaseScore(
        case_id=case.id,
        document_recall=_recall(case.expected_documents, value.retrieved_documents),
        page_recall=_recall(case.expected_pages, value.cited_pages),
        refusal_correct=value.did_refuse is case.should_refuse,
        search_requirement_met=value.search_count >= case.minimum_searches,
        latency_ms=value.latency_ms,
        total_tokens=value.prompt_tokens + value.completion_tokens,
    )


def summarize(scores: tuple[CaseScore, ...]) -> StrategySummary:
    if not scores:
        raise ValueError("benchmark_scores_required")
    count = len(scores)
    return StrategySummary(
        cases=count,
        document_recall=sum(row.document_recall for row in scores) / count,
        page_recall=sum(row.page_recall for row in scores) / count,
        correct_refusal_rate=sum(row.refusal_correct for row in scores) / count,
        search_requirement_rate=sum(row.search_requirement_met for row in scores) / count,
        mean_latency_ms=sum(row.latency_ms for row in scores) / count,
        mean_tokens=sum(row.total_tokens for row in scores) / count,
    )


def compare_observations(
    dataset: BenchmarkDataset,
    *,
    simple: tuple[StrategyObservation, ...],
    agentic: tuple[StrategyObservation, ...],
) -> ComparisonReport:
    """Produce a like-for-like report; incomplete or duplicate runs fail closed."""

    expected_ids = tuple(case.id for case in dataset.cases)

    def score(values: tuple[StrategyObservation, ...]) -> StrategySummary:
        by_id = {value.case_id: value for value in values}
        if len(by_id) != len(values) or set(by_id) != set(expected_ids):
            raise ValueError("benchmark_run_incomplete")
        return summarize(
            tuple(score_observation(case, by_id[case.id]) for case in dataset.cases)
        )

    simple_summary = score(simple)
    agentic_summary = score(agentic)
    return ComparisonReport(
        simple=simple_summary,
        agentic=agentic_summary,
        document_recall_delta=(
            agentic_summary.document_recall - simple_summary.document_recall
        ),
        page_recall_delta=agentic_summary.page_recall - simple_summary.page_recall,
        refusal_rate_delta=(
            agentic_summary.correct_refusal_rate
            - simple_summary.correct_refusal_rate
        ),
        latency_delta_ms=(
            agentic_summary.mean_latency_ms - simple_summary.mean_latency_ms
        ),
        token_delta=agentic_summary.mean_tokens - simple_summary.mean_tokens,
    )


def load_observations(path: Path) -> tuple[StrategyObservation, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or len(raw) > 500:
        raise ValueError("benchmark_observations_invalid")
    adapter = TypeAdapter(tuple[StrategyObservation, ...])
    return adapter.validate_python(raw)


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openrag-rag-comparison")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--simple", required=True, type=Path)
    parser.add_argument("--agentic", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = compare_observations(
            load_benchmark(args.dataset),
            simple=load_observations(args.simple),
            agentic=load_observations(args.agentic),
        )
        encoded = json.dumps(asdict(report), separators=(",", ":"), sort_keys=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError):
        return 2
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
