from uuid import uuid4

import pytest

from openrag.modules.runs.leases import RunLeaseClaim, claim_next_run


def test_run_lease_claim_contains_only_execution_identity() -> None:
    claim = RunLeaseClaim(
        run_id=uuid4(),
        org_id=uuid4(),
        workspace_id=uuid4(),
        chat_id=uuid4(),
        token=uuid4(),
        owner="runs@worker:task",
        attempt=2,
        recovered=True,
    )

    assert claim.attempt == 2
    assert claim.recovered is True
    assert "prompt" not in repr(claim)


async def test_claim_rejects_invalid_lease_before_database_access() -> None:
    with pytest.raises(ValueError, match="owner"):
        await claim_next_run(  # type: ignore[arg-type]
            None,
            owner="",
            lease_seconds=60,
        )
    with pytest.raises(ValueError, match="seconds"):
        await claim_next_run(  # type: ignore[arg-type]
            None,
            owner="worker",
            lease_seconds=5,
        )
