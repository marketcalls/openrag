from openrag.modules.events.streams import (
    DOCUMENT_EVENTS_STREAM,
    EVENT_TRANSPORT_FIELDS,
    stream_for_event_type,
)


def test_document_lifecycle_events_use_the_document_stream() -> None:
    assert (
        stream_for_event_type("document.version.lifecycle.v1")
        == DOCUMENT_EVENTS_STREAM
    )


def test_transport_has_exactly_two_attested_fields() -> None:
    assert EVENT_TRANSPORT_FIELDS == frozenset(
        {b"envelope_bytes", b"envelope_digest"}
    )
