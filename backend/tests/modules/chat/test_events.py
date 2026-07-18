import json

from openrag.modules.chat.events import (
    CitationRef,
    SourceRef,
    citations_event,
    done_event,
    error_event,
    retrieval_started_event,
    sources_event,
    token_event,
)


def test_encode_frame_format() -> None:
    frame = token_event("Hel\nlo").encode()

    assert frame.startswith("event: token\ndata: ")
    assert frame.endswith("\n\n")
    payload = frame.split("data: ", 1)[1].rstrip("\n")
    assert json.loads(payload) == {"delta": "Hel\nlo"}


def test_all_event_names_and_payloads() -> None:
    source = SourceRef(
        marker=1,
        document_id="document-1",
        filename="report.pdf",
        page=2,
        chunk_index=0,
        score=0.9,
        snippet="text",
    )
    citation = CitationRef(
        marker=1,
        document_id="document-1",
        chunk_ref="document-1:2:0",
        page=2,
        score=0.9,
    )

    assert retrieval_started_event().event == "retrieval_started"
    assert sources_event([source]).data == {
        "sources": [
            {
                "marker": 1,
                "document_id": "document-1",
                "filename": "report.pdf",
                "page": 2,
                "chunk_index": 0,
                "score": 0.9,
                "snippet": "text",
            }
        ]
    }
    assert citations_event([citation]).data == {
        "citations": [
            {
                "marker": 1,
                "document_id": "document-1",
                "chunk_ref": "document-1:2:0",
                "page": 2,
                "score": 0.9,
            }
        ]
    }
    done = done_event(
        message_id="message-1",
        prompt_tokens=10,
        completion_tokens=2,
        no_answer=False,
    )
    assert done.event == "done"
    assert done.data == {
        "message_id": "message-1",
        "prompt_tokens": 10,
        "completion_tokens": 2,
        "no_answer": False,
    }
    assert error_event("boom").data == {"detail": "boom"}
