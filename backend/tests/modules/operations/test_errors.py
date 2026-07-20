from openrag.modules.operations.errors import error_fingerprint, top_application_frame
from openrag.modules.operations.schemas import ErrorOccurrenceCreate


def _occurrence() -> ErrorOccurrenceCreate:
    return ErrorOccurrenceCreate(
        category="internal",
        code="internal.unhandled",
        service="api",
        environment="prod",
        exception_type="RuntimeError",
        top_frame="app.py:handle:10",
    )


def test_error_fingerprint_is_stable_and_content_free() -> None:
    occurrence = _occurrence()

    first = error_fingerprint(occurrence)
    second = error_fingerprint(occurrence)

    assert first == second
    assert len(first) == 64


def test_error_fingerprint_changes_for_safe_grouping_dimensions() -> None:
    occurrence = _occurrence()
    changed = occurrence.model_copy(update={"code": "internal.persistence"})

    assert error_fingerprint(occurrence) != error_fingerprint(changed)


def test_top_frame_excludes_exception_message_and_external_frames() -> None:
    try:
        raise RuntimeError("customer secret in exception")
    except RuntimeError as exc:
        frame = top_application_frame(exc)

    assert frame is not None
    assert "customer secret" not in frame
    assert "test_errors.py" in frame
