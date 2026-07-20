import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
OBSERVABILITY = ROOT / 'deploy' / 'observability'


def load_yaml(name: str) -> dict:  # type: ignore[type-arg]
    with (OBSERVABILITY / name).open(encoding='utf-8') as stream:
        result = yaml.safe_load(stream)
    assert isinstance(result, dict)
    return result


def test_collector_batches_sanitizes_and_routes_all_three_signals() -> None:
    config = load_yaml('otel-collector.yaml')

    pipelines = config['service']['pipelines']
    assert set(pipelines) == {'traces', 'metrics', 'logs'}
    for pipeline in pipelines.values():
        assert pipeline['processors'] == [
            'memory_limiter',
            'attributes/sanitize',
            'batch',
        ]
    deleted = {
        action['key']
        for action in config['processors']['attributes/sanitize']['actions']
        if action['action'] == 'delete'
    }
    assert {'authorization', 'prompt', 'document_text', 'memory', 'filename'} <= deleted


def test_alerts_use_low_cardinality_labels_and_have_runbooks() -> None:
    groups = load_yaml('alerts.yaml')['groups']
    rules = [rule for group in groups for rule in group['rules']]
    names = {rule['alert'] for rule in rules}

    assert {
        'OpenRAGHighP95Latency',
        'OpenRAGHighErrorRate',
        'OpenRAGHighNoAnswerRate',
        'OpenRAGProviderFailures',
        'OpenRAGQueueAgeHigh',
        'OpenRAGEventLoopLagHigh',
        'OpenRAGDatabasePoolSaturated',
        'OpenRAGRetrievalPassRateLow',
        'OpenRAGCitationCoverageLow',
        'OpenRAGEvaluationRegression',
    } <= names
    assert all(set(rule['labels']) == {'severity'} for rule in rules)
    assert all(rule['annotations']['runbook'].startswith('docs/runbooks/') for rule in rules)
    assert (ROOT / 'docs' / 'runbooks' / 'observability.md').is_file()


def test_grafana_dashboard_covers_runtime_quality_and_regressions() -> None:
    dashboard_path = OBSERVABILITY / 'grafana' / 'dashboards' / 'openrag-platform.json'
    dashboard = json.loads(dashboard_path.read_text(encoding='utf-8'))
    titles = {panel['title'] for panel in dashboard['panels']}

    assert {
        'Response latency and time to first token',
        'Error, no-answer, and provider failure rates',
        'Run and ingestion queue age',
        'Event-loop lag',
        'Database pool saturation',
        'Grounding quality',
        'Evaluation regression gates',
        'Centralized redacted errors',
    } <= titles
