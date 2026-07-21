from openrag.modules.chat.refusals import normalize_refusal_reason


def test_retrieval_reason_is_mapped_to_the_database_contract() -> None:
    assert normalize_refusal_reason("no_eligible_evidence") == "no_eligible_documents"
    assert normalize_refusal_reason("score_unavailable") == "incomplete_provenance"


def test_validation_and_authority_failures_are_fail_closed() -> None:
    assert normalize_refusal_reason("invalid_marker") == "citation_validation_failed"
    assert normalize_refusal_reason("authority_changed") == "incomplete_provenance"
    assert normalize_refusal_reason("strict_entailment_failed") == "entailment_failed"
    assert normalize_refusal_reason("unexpected_internal_reason") == (
        "citation_validation_failed"
    )


def test_database_native_reason_is_preserved() -> None:
    assert normalize_refusal_reason("below_threshold") == "below_threshold"
