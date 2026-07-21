"""Provision the fail-closed verifier binding required by governed workspaces."""

import hashlib
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.db import naive_utc
from openrag.modules.grounding.models import GroundingPolicy
from openrag.modules.models.models import Model, ModelProbe
from openrag.modules.tenancy.models import Workspace

_DEFAULT_ENTAILMENT_THRESHOLD = 0.9
_DEFAULT_DATASET_VERSION = "openrag-safe-default-v1"
_DEFAULT_DATASET_HASH = hashlib.sha256(
    b"openrag-safe-default-v1:claim-citation-binding:fail-closed-live-verifier"
).hexdigest()


async def provision_default_grounding_policy(
    session: AsyncSession,
    *,
    org_id: UUID,
    workspace_id: UUID,
    created_by: UUID,
) -> GroundingPolicy | None:
    """Bind a workspace to the latest measured verifier, idempotently.

    The default records no fabricated calibration statistics. It captures the
    exact successful capability-probe fingerprint and uses live, fail-closed
    verification for every grounded answer. A future calibrated policy can
    retire and replace this version without changing message provenance.
    """

    # Serialize provisioning per workspace so concurrent first queries cannot
    # race the unique-active-policy constraint.
    workspace = await session.scalar(
        select(Workspace)
        .where(Workspace.id == workspace_id, Workspace.org_id == org_id)
        .with_for_update()
    )
    if workspace is None:
        return None

    now = naive_utc()
    existing = await session.scalar(
        select(GroundingPolicy).where(
            GroundingPolicy.org_id == org_id,
            GroundingPolicy.workspace_id == workspace_id,
            GroundingPolicy.status == "active",
            or_(
                GroundingPolicy.effective_at.is_(None),
                GroundingPolicy.effective_at <= now,
            ),
            or_(
                GroundingPolicy.expires_at.is_(None),
                GroundingPolicy.expires_at > now,
            ),
        )
    )
    if existing is not None:
        return existing

    verifier_row = (
        await session.execute(
            select(Model, ModelProbe)
            .join(
                ModelProbe,
                (ModelProbe.model_id == Model.id) & (ModelProbe.revision == Model.probe_revision),
            )
            .where(
                Model.enabled.is_(True),
                Model.probe_status == "passed",
                Model.supports_chat_completion.is_(True),
                Model.supports_streaming.is_(True),
                Model.supports_structured_json.is_(True),
                Model.supports_verifier.is_(True),
                ModelProbe.status == "passed",
            )
            .order_by(
                Model.is_utility.desc(),
                Model.last_probed_at.desc().nulls_last(),
                Model.id,
            )
            .limit(1)
        )
    ).one_or_none()
    if verifier_row is None:
        return None

    verifier, probe = verifier_row
    latest_policy_version = await session.scalar(
        select(func.max(GroundingPolicy.policy_version)).where(
            GroundingPolicy.org_id == org_id,
            GroundingPolicy.workspace_id == workspace_id,
        )
    )
    provider_preset_version = verifier.provider_preset_version or (
        f"{verifier.provider_kind}-default-v1"
    )
    policy = GroundingPolicy(
        org_id=org_id,
        workspace_id=workspace_id,
        policy_version=(latest_policy_version or 0) + 1,
        verifier_model_id=verifier.id,
        binding_revision=f"model-probe-r{probe.revision}",
        provider_preset_version=provider_preset_version[:100],
        credential_fingerprint=probe.configuration_fingerprint,
        entailment_threshold=_DEFAULT_ENTAILMENT_THRESHOLD,
        calibration_dataset_version=_DEFAULT_DATASET_VERSION,
        calibration_dataset_hash=_DEFAULT_DATASET_HASH,
        calibration_sample_count=0,
        measured_false_support_rate=None,
        measured_false_refusal_rate=None,
        status="active",
        effective_at=now,
        created_by=created_by,
    )
    session.add(policy)
    await session.flush()
    return policy
