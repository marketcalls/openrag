"""Deterministic first-pass routing before any model or tool selection."""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

_MAX_QUERY_CHARS = 32_000
_SAFE_ORNAMENTS = " \t\r\n.!?,;:👋🙂😊🙏"

_GREETINGS = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "hey there",
        "hi there",
        "good morning",
        "good afternoon",
        "good evening",
    }
)
_ACKNOWLEDGEMENTS = frozenset(
    {
        "ok",
        "okay",
        "got it",
        "understood",
        "thanks",
        "thank you",
    }
)
_OPENRAG_HELP = frozenset(
    {
        "help",
        "what can you do",
        "how can you help",
        "how can you help me",
        "who are you",
        "what is openrag",
        "how do i use openrag",
    }
)
_THREAD_META = tuple(
    re.compile(pattern)
    for pattern in (
        r"what was my (?:previous|last) question",
        r"what was your (?:previous|last) answer",
        r"what did i ask (?:before|previously|last)",
        r"repeat my (?:previous|last) question",
        r"summarize (?:this|our) (?:chat|conversation)",
        r"what have we discussed",
    )
)
_REFERENTIAL_FOLLOWUPS = tuple(
    re.compile(pattern)
    for pattern in (
        r"tell me more(?: about (?:it|that|this))?",
        r"(?:explain|expand on) (?:it|that|this)",
        r"what about (?:it|that|this)",
        r"(?:why is|how does) that(?: work)?",
        r"continue",
        r"go on",
    )
)
_ANALYTICS_VERB = re.compile(
    r"\b(?:build|create|show|generate|make|plot|visualize|compare|summarize)\b"
)
_ANALYTICS_OBJECT = re.compile(
    r"\b(?:dashboard|chart|graph|table|plot|metrics?|kpis?|visualization)\b"
)


class QueryRoute(StrEnum):
    DIRECT = "direct"
    CONVERSATION = "conversation"
    RAG = "rag"
    ANALYTICS = "analytics"
    CLARIFY = "clarify"


@dataclass(frozen=True, slots=True)
class RouteDecision:
    route: QueryRoute
    reason_code: str
    retrieval_query: str | None


def _normalized(query: str) -> str:
    value = unicodedata.normalize("NFKC", query)
    return " ".join(value.split()).casefold()


def _route_text(query: str) -> str:
    return _normalized(query).strip(_SAFE_ORNAMENTS)


def _matches_any(value: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    return any(pattern.fullmatch(value) is not None for pattern in patterns)


def _last_user_turn(history: Sequence[tuple[str, str]]) -> str | None:
    for role, content in reversed(history):
        if role == "user" and content.strip():
            return " ".join(content.split())
    return None


def _contextual_retrieval_query(
    current: str,
    previous: str,
    *,
    max_chars: int,
) -> str:
    prefix = "Earlier user question: "
    separator = "\nFollow-up question: "
    fixed_chars = len(prefix) + len(separator) + len(current)
    available = max_chars - fixed_chars
    if available < 1:
        raise ValueError("retrieval budget cannot fit the current query")
    bounded_previous = previous[:available]
    return f"{prefix}{bounded_previous}{separator}{current}"


def decide_route(
    query: str,
    *,
    history: Sequence[tuple[str, str]],
    max_retrieval_chars: int = 2_000,
) -> RouteDecision:
    """Select a safe route without delegating policy to an LLM."""

    current = query.strip()
    if not current or len(query) > _MAX_QUERY_CHARS:
        raise ValueError("query must contain between 1 and 32000 characters")
    if not 1 <= max_retrieval_chars <= 2_000:
        raise ValueError("retrieval budget must be between 1 and 2000 characters")

    route_text = _route_text(current)
    if route_text in _GREETINGS:
        return RouteDecision(QueryRoute.DIRECT, "safe_greeting", None)
    if route_text in _ACKNOWLEDGEMENTS:
        return RouteDecision(QueryRoute.DIRECT, "safe_acknowledgement", None)
    if route_text in _OPENRAG_HELP:
        return RouteDecision(QueryRoute.DIRECT, "openrag_help", None)
    if _matches_any(route_text, _THREAD_META):
        return RouteDecision(QueryRoute.CONVERSATION, "thread_meta", None)

    if _matches_any(route_text, _REFERENTIAL_FOLLOWUPS):
        previous = _last_user_turn(history)
        if previous is None:
            return RouteDecision(
                QueryRoute.CLARIFY,
                "missing_followup_context",
                None,
            )
        return RouteDecision(
            QueryRoute.RAG,
            "referential_followup",
            _contextual_retrieval_query(
                current,
                previous,
                max_chars=max_retrieval_chars,
            ),
        )

    if _ANALYTICS_VERB.search(route_text) and _ANALYTICS_OBJECT.search(route_text):
        if len(current) > max_retrieval_chars:
            raise ValueError("query exceeds retrieval budget")
        return RouteDecision(QueryRoute.ANALYTICS, "analytical_request", current)

    if len(current) > max_retrieval_chars:
        raise ValueError("query exceeds retrieval budget")
    return RouteDecision(QueryRoute.RAG, "substantive_default", current)
