from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from openrag.modules.documents.lifecycle import DocumentVersionState
from openrag.modules.documents.lifecycle_projection import (
    consume_document_lifecycle_batch,
    is_current_eligible,
    project_document_lifecycle,
)
from openrag.modules.events.envelopes import (
    DocumentVersionLifecycleV1,
    build_envelope,
)


@pytest.mark.parametrize(
    ("state", "provenance_state", "superseded", "eligible"),
    [
        ("approved", "ready", False, True),
        ("approved", "ready", True, False),
        ("approved", "building", False, False),
        ("review", "ready", False, False),
        ("obsolete", "ready", False, False),
    ],
)
def test_current_eligibility_requires_exact_authoritative_state(
    state: str,
    provenance_state: str,
    superseded: bool,
    eligible: bool,
) -> None:
    version = SimpleNamespace(
        state=state,
        provenance_state=provenance_state,
        superseded_by_id=("successor" if superseded else None),
    )

    assert is_current_eligible(version) is eligible


@pytest.mark.parametrize(
    ("consumer", "batch_size", "reclaim_idle_ms"),
    [
        ("", 1, 30_000),
        ("worker-a", 0, 30_000),
        ("worker-a", 101, 30_000),
        ("worker-a", 1, 29_999),
    ],
)
async def test_lifecycle_batch_rejects_unbounded_configuration(
    consumer: str,
    batch_size: int,
    reclaim_idle_ms: int,
) -> None:
    with pytest.raises(ValueError, match="invalid"):
        await consume_document_lifecycle_batch(
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            consumer=consumer,
            batch_size=batch_size,
            reclaim_idle_ms=reclaim_idle_ms,
        )


async def test_projection_upsert_is_revision_monotonic_and_content_free() -> None:
    version_id = UUID("84000000-0000-0000-0000-000000000004")
    version = SimpleNamespace(
        id=version_id,
        lifecycle_revision=4,
        state="approved",
        provenance_state="ready",
        superseded_by_id=None,
    )

    class RecordingSession:
        statement: object | None = None

        async def scalar(self, _statement: object) -> object:
            return version

        async def execute(self, statement: object) -> None:
            self.statement = statement

    session = RecordingSession()
    envelope = build_envelope(
        payload=DocumentVersionLifecycleV1(
            document_id=UUID("81000000-0000-0000-0000-000000000001"),
            previous_state=DocumentVersionState.REVIEW,
            new_state=DocumentVersionState.APPROVED,
        ),
        event_id=UUID("86000000-0000-0000-0000-000000000006"),
        org_id=UUID("82000000-0000-0000-0000-000000000002"),
        workspace_id=UUID("83000000-0000-0000-0000-000000000003"),
        aggregate_id=version_id,
        lifecycle_revision=4,
        correlation_id=UUID("85000000-0000-0000-0000-000000000005"),
        occurred_at=datetime(2026, 7, 20, 2, tzinfo=UTC),
    )

    await project_document_lifecycle(session, envelope)  # type: ignore[arg-type]

    assert session.statement is not None
    compiled = str(
        session.statement.compile(dialect=postgresql.dialect())  # type: ignore[union-attr]
    )
    assert "ON CONFLICT ON CONSTRAINT uq_document_version_projections_version" in compiled
    assert "document_version_projections.applied_revision < excluded.applied_revision" in compiled
    assert "document_text" not in compiled
