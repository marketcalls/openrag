from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from openrag.modules.retrieval.authority import (
    MAX_CANDIDATES,
    AuthoritySnapshot,
    CandidateIdentity,
    candidate_from_payload,
    candidate_is_authorized,
    validate_candidate_batch,
)

ORG_ID = UUID("81000000-0000-0000-0000-000000000001")
WORKSPACE_ID = UUID("82000000-0000-0000-0000-000000000002")
VERSION_ID = UUID("83000000-0000-0000-0000-000000000003")
SPAN_ID = UUID("84000000-0000-0000-0000-000000000004")
NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)
CONTENT_HASH = "a" * 64


def snapshot(**overrides: object) -> AuthoritySnapshot:
    values: dict[str, object] = {
        "org_id": ORG_ID,
        "workspace_id": WORKSPACE_ID,
        "document_version_id": VERSION_ID,
        "evidence_span_id": SPAN_ID,
        "state": "approved",
        "provenance_state": "ready",
        "superseded_by_id": None,
        "effective_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=1),
        "source_deleted_at": None,
        "source_storage_key": "authority/source",
        "acl_policy": None,
        "content_hash": CONTENT_HASH,
    }
    values.update(overrides)
    return AuthoritySnapshot(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [
        {"org_id": UUID("91000000-0000-0000-0000-000000000001")},
        {"workspace_id": UUID("92000000-0000-0000-0000-000000000002")},
        {"state": "superseded"},
        {"provenance_state": "building"},
        {"superseded_by_id": UUID("93000000-0000-0000-0000-000000000003")},
        {"effective_at": NOW + timedelta(seconds=1)},
        {"expires_at": NOW},
        {"source_deleted_at": NOW - timedelta(seconds=1)},
        {"source_storage_key": None},
        {"acl_policy": {"mode": "roles", "roles": ["hse-manager"]}},
        {"content_hash": "b" * 64},
    ],
)
def test_candidate_authority_fails_closed_for_every_invalid_dimension(
    overrides: dict[str, object],
) -> None:
    assert candidate_is_authorized(
        snapshot(**overrides),
        org_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        now=NOW,
        expected_content_hash=CONTENT_HASH,
    ) is False


def test_candidate_authority_accepts_exact_current_workspace_evidence() -> None:
    assert candidate_is_authorized(
        snapshot(),
        org_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        now=NOW,
        expected_content_hash=CONTENT_HASH,
    ) is True


def test_candidate_batch_is_bounded_and_deduplicated_by_evidence_identity() -> None:
    candidate = CandidateIdentity(
        document_version_id=VERSION_ID,
        evidence_span_id=SPAN_ID,
        content_hash=CONTENT_HASH,
        fused_score=0.8,
    )

    assert validate_candidate_batch([candidate, candidate]) == (candidate,)

    with pytest.raises(ValueError, match="candidate_limit_exceeded"):
        validate_candidate_batch([candidate] * (MAX_CANDIDATES + 1))


def test_qdrant_payload_parser_accepts_only_complete_bounded_identity() -> None:
    candidate = candidate_from_payload(
        {
            "document_version_id": str(VERSION_ID),
            "evidence_span_id": str(SPAN_ID),
            "content_hash": CONTENT_HASH,
            "text": "must remain untrusted",
        },
        fused_score=0.8,
    )

    assert candidate == CandidateIdentity(
        document_version_id=VERSION_ID,
        evidence_span_id=SPAN_ID,
        content_hash=CONTENT_HASH,
        fused_score=0.8,
    )
    assert candidate_from_payload({}, fused_score=0.8) is None
    assert candidate_from_payload(
        {
            "document_version_id": str(VERSION_ID),
            "evidence_span_id": str(SPAN_ID),
            "content_hash": "not-a-hash",
        },
        fused_score=0.8,
    ) is None
