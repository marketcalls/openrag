from datetime import datetime, timedelta
from uuid import uuid4

from openrag.modules.memory.models import MemoryRecord
from openrag.modules.memory.selection import rank_memory_candidates


def memory(
    *,
    key: str,
    content: str,
    importance: float,
    memory_type: str = "semantic",
) -> MemoryRecord:
    now = datetime(2026, 7, 20, 12, 0, 0)
    return MemoryRecord(
        id=uuid4(),
        org_id=uuid4(),
        workspace_id=uuid4(),
        user_id=uuid4(),
        client_request_id=uuid4(),
        canonical_key=key,
        content=content,
        memory_type=memory_type,
        scope="user_workspace",
        status="active",
        confidence=1,
        importance=importance,
        sensitivity="internal",
        policy_version="explicit-user-memory-v1",
        source_trust="explicit_user",
        content_hash="a" * 64,
        suppression_fingerprint="b" * 64,
        created_at=now - timedelta(days=1),
        updated_at=now,
    )


def test_selection_is_relevant_bounded_and_deduplicated_by_key() -> None:
    candidates = [
        memory(
            key="project.name",
            content="The project is Apollo.",
            importance=0.7,
        ),
        memory(
            key="response.style",
            content="Prefer concise answers.",
            importance=0.9,
        ),
        memory(
            key="project.name",
            content="The old project was Atlas.",
            importance=0.2,
        ),
        memory(
            key="meeting.outcome",
            content="The hiring review was postponed.",
            importance=1,
            memory_type="episodic",
        ),
    ]

    selected = rank_memory_candidates(
        candidates,
        query="Tell me about project Apollo",
        semantic_enabled=True,
        episodic_enabled=True,
        max_items=2,
        max_tokens=100,
    )

    assert [item.canonical_key for item in selected] == [
        "project.name",
        "response.style",
    ]
    assert len({item.canonical_key for item in selected}) == len(selected)


def test_irrelevant_episodic_memory_and_disabled_types_are_excluded() -> None:
    candidates = [
        memory(
            key="meeting.outcome",
            content="The hiring review was postponed.",
            importance=1,
            memory_type="episodic",
        ),
        memory(
            key="response.style",
            content="Prefer concise answers.",
            importance=0.8,
        ),
    ]

    assert [
        item.canonical_key
        for item in rank_memory_candidates(
            candidates,
            query="hello",
            semantic_enabled=True,
            episodic_enabled=True,
        )
    ] == ["response.style"]
    assert (
        rank_memory_candidates(
            candidates,
            query="hiring review",
            semantic_enabled=False,
            episodic_enabled=False,
        )
        == ()
    )


def test_selection_never_exceeds_token_or_item_budget() -> None:
    candidates = [
        memory(key=f"fact.{index}", content="value " * 20, importance=1)
        for index in range(20)
    ]
    selected = rank_memory_candidates(
        candidates,
        query="value",
        semantic_enabled=True,
        episodic_enabled=False,
        max_items=8,
        max_tokens=30,
    )

    assert len(selected) <= 2
