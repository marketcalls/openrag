import pytest

from openrag.modules.documents.lifecycle import (
    InvalidDocumentTransition,
    ensure_transition,
    normalize_version_label,
    validate_section_path,
)


def test_version_key_is_nfkc_casefolded_and_sequence_is_not_client_data() -> None:
    assert normalize_version_label("  REV  ７ ") == ("REV 7", "rev 7")


def test_section_path_is_bounded() -> None:
    assert validate_section_path(["Emergency", "Evacuation"]) == (
        "Emergency",
        "Evacuation",
    )
    with pytest.raises(ValueError, match="at most 8"):
        validate_section_path([str(index) for index in range(9)])

    with pytest.raises(ValueError, match="at most 200"):
        validate_section_path(["x" * 201])


def test_processing_cannot_skip_review() -> None:
    with pytest.raises(InvalidDocumentTransition):
        ensure_transition("processing", "approved")


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("draft", "processing"),
        ("processing", "review"),
        ("processing", "failed"),
        ("failed", "processing"),
        ("review", "approved"),
        ("review", "rejected"),
        ("approved", "superseded"),
        ("approved", "obsolete"),
    ],
)
def test_exact_lifecycle_edges_are_allowed(current: str, target: str) -> None:
    ensure_transition(current, target)
