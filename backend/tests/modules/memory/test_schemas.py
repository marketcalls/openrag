from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from openrag.modules.memory.schemas import MemoryCreate, MemoryPatch, MemoryPreferencePatch


def test_explicit_memory_create_accepts_bounded_user_scope() -> None:
    body = MemoryCreate(
        client_request_id=uuid4(),
        canonical_key="response.style",
        content="  Prefer concise answers.  ",
        structured_value={"tone": "concise"},
        memory_type="semantic",
        scope="user_workspace",
        confidence=0.9,
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
    )

    assert body.content == "Prefer concise answers."
    assert body.canonical_key == "response.style"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scope", "workspace_shared"),
        ("scope", "user_org"),
        ("memory_type", "procedural"),
        ("canonical_key", "Not Valid"),
        ("content", " "),
        ("confidence", 1.1),
    ],
)
def test_memory_create_rejects_unapproved_or_unbounded_values(
    field: str,
    value: object,
) -> None:
    payload: dict[str, object] = {
        "client_request_id": uuid4(),
        "canonical_key": "response.style",
        "content": "Prefer concise answers.",
        "memory_type": "semantic",
        "scope": "user_workspace",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        MemoryCreate.model_validate(payload)


def test_memory_create_rejects_oversized_structured_value() -> None:
    with pytest.raises(ValidationError, match="8192"):
        MemoryCreate(
            client_request_id=uuid4(),
            canonical_key="response.style",
            content="Prefer concise answers.",
            structured_value={"payload": "x" * 9000},
            memory_type="semantic",
            scope="user_workspace",
        )


def test_memory_patch_requires_a_change_and_request_identity() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        MemoryPatch(client_request_id=uuid4())

    patch = MemoryPatch(
        client_request_id=uuid4(),
        content="Prefer tables for comparisons.",
    )
    assert patch.content == "Prefer tables for comparisons."


def test_memory_preference_patch_requires_a_change() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        MemoryPreferencePatch()
