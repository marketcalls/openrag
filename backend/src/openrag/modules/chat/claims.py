"""Deterministic claim-to-citation binding for grounded answers."""

import hashlib
import re
from dataclasses import dataclass

_MARKER_RE = re.compile(r"\[(\d{1,3})\]")
_WHITESPACE_RE = re.compile(r"\s+")
_MAX_ANSWER_CHARS = 100_000
_MAX_CLAIMS = 256


@dataclass(frozen=True, slots=True)
class ClaimBindingResult:
    valid: bool
    reason_code: str | None
    by_marker: dict[int, tuple[str, ...]]


def _claim_id(text: str) -> str:
    normalized = _WHITESPACE_RE.sub(" ", text).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def bind_cited_claims(answer: str, *, max_marker: int) -> ClaimBindingResult:
    """Require every non-empty answer line to bind to valid source markers."""

    if not 1 <= max_marker <= 32:
        raise ValueError("max_marker must be between 1 and 32")
    if len(answer) > _MAX_ANSWER_CHARS:
        raise ValueError("answer exceeds claim binding limit")
    if not answer.strip():
        return ClaimBindingResult(False, "empty_answer", {})

    lines = [line.strip() for line in answer.splitlines() if line.strip()]
    if len(lines) > _MAX_CLAIMS:
        raise ValueError("answer exceeds claim count limit")

    bindings: dict[int, list[str]] = {}
    for line in lines:
        raw_markers = [int(match.group(1)) for match in _MARKER_RE.finditer(line)]
        if raw_markers and any(marker > max_marker for marker in raw_markers):
            return ClaimBindingResult(False, "invalid_marker", {})
        claim_text = _MARKER_RE.sub("", line)
        if not any(character.isalnum() for character in claim_text):
            return ClaimBindingResult(False, "empty_claim", {})
        if not raw_markers:
            return ClaimBindingResult(False, "uncited_claim", {})

        claim_id = _claim_id(claim_text)
        for marker in dict.fromkeys(raw_markers):
            marker_claims = bindings.setdefault(marker, [])
            if claim_id not in marker_claims:
                marker_claims.append(claim_id)

    return ClaimBindingResult(
        True,
        None,
        {marker: tuple(claim_ids) for marker, claim_ids in bindings.items()},
    )
