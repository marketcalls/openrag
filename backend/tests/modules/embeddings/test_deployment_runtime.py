from datetime import datetime
from typing import Any, cast
from uuid import UUID

import pytest

from openrag.modules.embeddings.deployment_runtime import (
    DeploymentScanClaim,
    claim_deployment_scan,
    scan_claimed_deployment_page,
)
from openrag.worker.celery_app import celery_app


async def test_deployment_scanner_rejects_unbounded_coordination_inputs() -> None:
    unused_factory = cast(Any, None)
    with pytest.raises(ValueError, match="owner"):
        await claim_deployment_scan(unused_factory, owner="")
    with pytest.raises(ValueError, match="lease"):
        await claim_deployment_scan(
            unused_factory,
            owner="scanner",
            lease_seconds=29,
        )

    claim = DeploymentScanClaim(
        deployment_id=UUID("42be9246-631d-4a84-b669-a48953550895"),
        generation_id=UUID("566e45b0-051c-4d86-87b3-6a528c7935c2"),
        profile_version=f"embedding/v1/{'a' * 64}",
        dimension=1024,
        owner="scanner",
        lease_token=UUID("4bd2a478-9a6a-4e9d-8385-766a4fbed7ee"),
        lease_expires_at=datetime(2026, 7, 20, 20),
    )
    with pytest.raises(ValueError, match="page_size"):
        await scan_claimed_deployment_page(
            unused_factory,
            claim,
            page_size=1001,
        )


def test_embedding_scanner_is_scheduled_on_the_isolated_ingestion_queue() -> None:
    schedule = celery_app.conf.beat_schedule["scan-embedding-deployment"]

    assert schedule["task"] == "embeddings.scan_deployment"
    assert schedule["options"]["queue"] == "ingestion"
