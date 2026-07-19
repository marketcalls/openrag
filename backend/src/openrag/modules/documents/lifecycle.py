"""Bounded document-authority lifecycle and normalization contracts."""

import re
import unicodedata
from enum import StrEnum

_MAX_VERSION_LABEL_LENGTH = 200
_MAX_SECTION_DEPTH = 8
_MAX_SECTION_ELEMENT_LENGTH = 200
_WHITESPACE = re.compile(r"\s+")

LEGACY_VERSION_LABEL = "Legacy 1"
LEGACY_VERSION_KEY = "legacy 1"
LEGACY_PARSER_PROFILE_VERSION = "legacy/parser-v1"
LEGACY_OCR_PROFILE_VERSION = "legacy/ocr-unknown-v1"
LEGACY_CHUNKING_PROFILE_VERSION = "legacy/chunking-v1"
LEGACY_EMBEDDING_PROFILE_VERSION = "legacy/embedding-v1"
LEGACY_INDEX_PROFILE_VERSION = "legacy/index-v1"
LEGACY_CITATION_SECTION = "Legacy import"
LEGACY_CITATION_CONTENT_HASH = "legacy-unverified"
LEGACY_CITATION_VERIFICATION_STATE = "legacy_unverified"
NO_OCR_PROFILE_VERSION = "none/v1"


class DocumentVersionState(StrEnum):
    DRAFT = "draft"
    PROCESSING = "processing"
    REVIEW = "review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    OBSOLETE = "obsolete"
    FAILED = "failed"


class ProvenanceState(StrEnum):
    NONE = "none"
    LEGACY_PENDING = "legacy_pending"
    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"


class AnswerStatus(StrEnum):
    GROUNDED = "grounded"
    CITED_CONFLICT = "cited_conflict"
    REFUSED = "refused"


class RefusalReason(StrEnum):
    NO_ELIGIBLE_DOCUMENTS = "no_eligible_documents"
    NO_CANDIDATES = "no_candidates"
    BELOW_THRESHOLD = "below_threshold"
    INCOMPLETE_PROVENANCE = "incomplete_provenance"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    INDEX_PROJECTION_LAG = "index_projection_lag"
    ENTAILMENT_FAILED = "entailment_failed"
    CITATION_VALIDATION_FAILED = "citation_validation_failed"


class InvalidDocumentTransition(ValueError):
    """Raised when a document version attempts an unapproved lifecycle edge."""


_ALLOWED_TRANSITIONS: frozenset[tuple[DocumentVersionState, DocumentVersionState]] = (
    frozenset(
        {
            (DocumentVersionState.DRAFT, DocumentVersionState.PROCESSING),
            (DocumentVersionState.PROCESSING, DocumentVersionState.REVIEW),
            (DocumentVersionState.PROCESSING, DocumentVersionState.FAILED),
            (DocumentVersionState.FAILED, DocumentVersionState.PROCESSING),
            (DocumentVersionState.REVIEW, DocumentVersionState.APPROVED),
            (DocumentVersionState.REVIEW, DocumentVersionState.REJECTED),
            (DocumentVersionState.APPROVED, DocumentVersionState.SUPERSEDED),
            (DocumentVersionState.APPROVED, DocumentVersionState.OBSOLETE),
        }
    )
)


def normalize_version_label(value: str) -> tuple[str, str]:
    """Return an NFKC display label and casefolded lookup key."""

    display = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value)).strip()
    if not display:
        raise ValueError("version label must not be empty")
    if len(display) > _MAX_VERSION_LABEL_LENGTH:
        raise ValueError(f"version label must be at most {_MAX_VERSION_LABEL_LENGTH} characters")
    lookup_key = display.casefold()
    if len(lookup_key) > _MAX_VERSION_LABEL_LENGTH:
        raise ValueError(
            f"version lookup key must be at most {_MAX_VERSION_LABEL_LENGTH} characters"
        )
    return display, lookup_key


def validate_section_path(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Normalize a bounded, non-empty document section hierarchy."""

    if not values:
        raise ValueError("section path must contain at least one element")
    if len(values) > _MAX_SECTION_DEPTH:
        raise ValueError(f"section path may contain at most {_MAX_SECTION_DEPTH} elements")
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("section path elements must be strings")
        section = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value)).strip()
        if not section:
            raise ValueError("section path elements must not be empty")
        if len(section) > _MAX_SECTION_ELEMENT_LENGTH:
            raise ValueError(
                f"section path elements must be at most {_MAX_SECTION_ELEMENT_LENGTH} characters"
            )
        normalized.append(section)
    return tuple(normalized)


def ensure_transition(
    current: str | DocumentVersionState,
    target: str | DocumentVersionState,
) -> None:
    """Reject every lifecycle edge outside the explicit authority graph."""

    try:
        edge = (DocumentVersionState(current), DocumentVersionState(target))
    except ValueError as exc:
        raise InvalidDocumentTransition(
            f"unknown document transition: {current} -> {target}"
        ) from exc
    if edge not in _ALLOWED_TRANSITIONS:
        raise InvalidDocumentTransition(f"invalid document transition: {edge[0]} -> {edge[1]}")
