"""Stable Redis Stream names and the deliberately tiny wire contract."""

from openrag.modules.events.envelopes import LIFECYCLE_EVENT_TYPE

DOCUMENT_EVENTS_STREAM = "openrag:events:documents"
DOCUMENT_EVENTS_DLQ_STREAM = "openrag:events:documents:dlq"
EVENT_TRANSPORT_FIELDS = frozenset(
    {b"envelope_bytes", b"envelope_digest"}
)


def stream_for_event_type(event_type: str) -> str:
    """Resolve only registered schemas to bounded, namespaced streams."""

    if event_type == LIFECYCLE_EVENT_TYPE:
        return DOCUMENT_EVENTS_STREAM
    raise ValueError("schema_not_registered")
