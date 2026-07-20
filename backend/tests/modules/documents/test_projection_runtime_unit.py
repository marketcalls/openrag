from uuid import UUID

from openrag.modules.documents.projection_runtime import (
    EligibilityProjectionClaim,
    apply_projection_to_qdrant,
)


async def test_qdrant_projection_is_exactly_tenant_and_version_scoped() -> None:
    claim = EligibilityProjectionClaim(
        projection_id=UUID("80000000-0000-0000-0000-000000000001"),
        org_id=UUID("81000000-0000-0000-0000-000000000001"),
        workspace_id=UUID("82000000-0000-0000-0000-000000000002"),
        document_version_id=UUID("83000000-0000-0000-0000-000000000003"),
        revision=7,
        is_current_eligible=False,
        generation_id=UUID("84000000-0000-0000-0000-000000000004"),
        physical_collection="openrag_authority_v1_84000000000000000000000000000004",
        owner="projection-worker-a",
        lease_token=UUID("85000000-0000-0000-0000-000000000005"),
    )

    class RecordingQdrant:
        calls: list[dict[str, object]] = []

        async def set_payload(self, **kwargs: object) -> object:
            self.calls.append(kwargs)
            return object()

    qdrant = RecordingQdrant()

    await apply_projection_to_qdrant(claim, qdrant)

    assert len(qdrant.calls) == 1
    call = qdrant.calls[0]
    assert call["collection_name"] == claim.physical_collection
    assert call["payload"] == {
        "is_current_approved": False,
        "projection_revision": 7,
    }
    assert call["wait"] is True
    selector = call["points"]
    conditions = selector.filter.must  # type: ignore[union-attr]
    assert {condition.key: condition.match.value for condition in conditions} == {
        "tenant_id": str(claim.org_id),
        "workspace_id": str(claim.workspace_id),
        "document_version_id": str(claim.document_version_id),
    }
