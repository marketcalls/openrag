from datetime import datetime
from uuid import uuid4

from openrag.modules.memory.service import (
    content_digest,
    decode_memory_cursor,
    encode_memory_cursor,
    normalize_canonical_key,
    suppression_fingerprint,
)


def test_memory_digests_are_stable_and_scope_bound() -> None:
    workspace_id = uuid4()
    user_id = uuid4()

    digest = content_digest("  Prefer concise answers. ", {"tone": "concise"})
    assert digest == content_digest("Prefer concise answers.", {"tone": "concise"})
    assert digest != content_digest("Prefer long answers.", {"tone": "concise"})

    fingerprint = suppression_fingerprint(
        workspace_id=workspace_id,
        user_id=user_id,
        canonical_key="response.style",
        content_hash=digest,
    )
    assert fingerprint != suppression_fingerprint(
        workspace_id=uuid4(),
        user_id=user_id,
        canonical_key="response.style",
        content_hash=digest,
    )


def test_memory_key_and_cursor_are_canonical_and_round_trip() -> None:
    assert normalize_canonical_key("  RESPONSE.Style ") == "response.style"
    created_at = datetime(2026, 7, 20, 12, 30, 0)
    cursor = encode_memory_cursor(created_at, uuid4())
    assert decode_memory_cursor(cursor)[0] == created_at
