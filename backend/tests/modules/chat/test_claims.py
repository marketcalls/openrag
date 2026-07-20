import pytest

from openrag.modules.chat.claims import bind_cited_claims


def test_binds_each_claim_line_to_its_exact_source_markers() -> None:
    result = bind_cited_claims(
        "PPE must be inspected before use [1].\nReplace damaged PPE [1][2].",
        max_marker=2,
    )

    assert result.valid is True
    assert result.reason_code is None
    assert set(result.by_marker) == {1, 2}
    assert len(result.by_marker[1]) == 2
    assert len(result.by_marker[2]) == 1
    assert all(len(claim_id) == 64 for ids in result.by_marker.values() for claim_id in ids)


def test_claim_ids_are_stable_for_equivalent_whitespace() -> None:
    first = bind_cited_claims("Wear   gloves [1].", max_marker=1)
    second = bind_cited_claims("  Wear gloves [1].  ", max_marker=1)

    assert first.by_marker == second.by_marker


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        ("This unsupported line has no source.", "uncited_claim"),
        ("[1]", "empty_claim"),
        ("A claim [2].", "invalid_marker"),
        ("", "empty_answer"),
    ],
)
def test_rejects_answers_without_complete_valid_marker_binding(
    answer: str,
    reason: str,
) -> None:
    result = bind_cited_claims(answer, max_marker=1)

    assert result.valid is False
    assert result.reason_code == reason
    assert result.by_marker == {}


def test_rejects_unbounded_marker_catalog() -> None:
    with pytest.raises(ValueError, match="max_marker"):
        bind_cited_claims("Claim [1].", max_marker=33)
