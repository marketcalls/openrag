"""Canonical, database-safe refusal taxonomy for grounded answers."""

from typing import Final

CONTROLLED_REFUSAL_REASONS: Final[frozenset[str]] = frozenset(
    {
        "no_eligible_documents",
        "no_candidates",
        "below_threshold",
        "incomplete_provenance",
        "conflicting_evidence",
        "index_projection_lag",
        "entailment_failed",
        "citation_validation_failed",
    }
)

_ALIASES: Final[dict[str, str]] = {
    "no_eligible_evidence": "no_eligible_documents",
    "score_unavailable": "incomplete_provenance",
    "grounding_policy_unavailable": "incomplete_provenance",
    "authority_identity_missing": "incomplete_provenance",
    "authority_identity_invalid": "incomplete_provenance",
    "authority_changed": "incomplete_provenance",
    "validation_policy_changed": "citation_validation_failed",
    "invalid_marker": "citation_validation_failed",
    "incomplete_claim_binding": "citation_validation_failed",
    "strict_entailment_failed": "entailment_failed",
}


def normalize_refusal_reason(reason: str) -> str:
    """Map internal detail to the bounded taxonomy enforced by PostgreSQL."""

    if reason in CONTROLLED_REFUSAL_REASONS:
        return reason
    return _ALIASES.get(reason, "citation_validation_failed")
