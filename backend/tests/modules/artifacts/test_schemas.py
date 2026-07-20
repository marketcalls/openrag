import copy

import pytest
from pydantic import ValidationError

from openrag.modules.artifacts.schemas import AnalyticsResponseV1


def valid_payload() -> dict[str, object]:
    return {
        "schema_version": "analytics.v1",
        "title": "Revenue dashboard",
        "subtitle": "Approved Q4 revenue summary",
        "kpis": [
            {
                "label": "Q4 revenue",
                "value": "$4.83M",
                "detail": "Across October to December",
                "trend": "up",
                "source_markers": [1],
            }
        ],
        "blocks": [
            {
                "kind": "bar_chart",
                "title": "Monthly revenue",
                "x_label": "Month",
                "y_label": "Revenue in millions",
                "categories": ["October", "November", "December"],
                "series": [{"name": "Revenue", "values": [1.42, 1.57, 1.84]}],
                "source_markers": [1, 2],
            },
            {
                "kind": "table",
                "title": "Revenue summary",
                "columns": [
                    {"key": "month", "label": "Month", "format": "text"},
                    {"key": "revenue", "label": "Revenue", "format": "currency"},
                ],
                "rows": [
                    {"month": "October", "revenue": 1.42},
                    {"month": "November", "revenue": 1.57},
                ],
                "source_markers": [1],
            },
            {
                "kind": "explainer",
                "title": "What changed",
                "body_markdown": "Revenue increased in each reported month [1].",
                "source_markers": [1],
            },
        ],
        "suggested_followups": ["Break this down by product line"],
    }


def test_analytics_v1_accepts_closed_bounded_blocks() -> None:
    artifact = AnalyticsResponseV1.model_validate(valid_payload())

    assert artifact.schema_version == "analytics.v1"
    assert artifact.kpis[0].source_markers == [1]
    assert [block.kind for block in artifact.blocks] == [
        "bar_chart",
        "table",
        "explainer",
    ]


def test_analytics_v1_rejects_unknown_schema_fields_and_block_kinds() -> None:
    payload = valid_payload()
    payload["component"] = "CustomRevenueWidget"
    with pytest.raises(ValidationError):
        AnalyticsResponseV1.model_validate(payload)

    payload = valid_payload()
    blocks = copy.deepcopy(payload["blocks"])
    assert isinstance(blocks, list)
    blocks[0]["kind"] = "vega"  # type: ignore[index]
    payload["blocks"] = blocks
    with pytest.raises(ValidationError):
        AnalyticsResponseV1.model_validate(payload)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "<script>alert(1)</script>",
        "<iframe srcdoc='unsafe'></iframe>",
        "javascript:alert(1)",
        "https://attacker.example/pixel",
        "data:text/html,unsafe",
        "unsafe\x00text",
    ],
)
def test_analytics_v1_rejects_executable_url_or_control_text(
    unsafe_text: str,
) -> None:
    payload = valid_payload()
    payload["title"] = unsafe_text

    with pytest.raises(ValidationError):
        AnalyticsResponseV1.model_validate(payload)


def test_analytics_v1_rejects_invalid_or_duplicate_source_markers() -> None:
    for markers in ([], [0], [1, 1], [1, 1000]):
        payload = valid_payload()
        kpis = copy.deepcopy(payload["kpis"])
        assert isinstance(kpis, list)
        kpis[0]["source_markers"] = markers  # type: ignore[index]
        payload["kpis"] = kpis
        with pytest.raises(ValidationError):
            AnalyticsResponseV1.model_validate(payload)


def test_analytics_v1_rejects_non_finite_or_misaligned_chart_values() -> None:
    for values in ([1.0, float("nan"), 2.0], [1.0, 2.0]):
        payload = valid_payload()
        blocks = copy.deepcopy(payload["blocks"])
        assert isinstance(blocks, list)
        blocks[0]["series"][0]["values"] = values  # type: ignore[index]
        payload["blocks"] = blocks
        with pytest.raises(ValidationError):
            AnalyticsResponseV1.model_validate(payload)


def test_analytics_v1_rejects_table_rows_outside_declared_schema() -> None:
    payload = valid_payload()
    blocks = copy.deepcopy(payload["blocks"])
    assert isinstance(blocks, list)
    blocks[1]["rows"] = [{"month": "October", "revenue": 1.42, "formula": "=1+1"}]  # type: ignore[index]
    payload["blocks"] = blocks

    with pytest.raises(ValidationError):
        AnalyticsResponseV1.model_validate(payload)


def test_analytics_v1_rejects_boolean_table_scalars() -> None:
    payload = valid_payload()
    blocks = copy.deepcopy(payload["blocks"])
    assert isinstance(blocks, list)
    blocks[1]["rows"] = [{"month": "October", "revenue": True}]  # type: ignore[index]
    payload["blocks"] = blocks

    with pytest.raises(ValidationError):
        AnalyticsResponseV1.model_validate(payload)


def test_analytics_v1_rejects_serialized_payload_above_event_budget() -> None:
    payload = valid_payload()
    blocks = copy.deepcopy(payload["blocks"])
    assert isinstance(blocks, list)
    blocks[2]["body_markdown"] = "x" * 48_000  # type: ignore[index]
    payload["blocks"] = blocks

    with pytest.raises(ValidationError):
        AnalyticsResponseV1.model_validate(payload)
