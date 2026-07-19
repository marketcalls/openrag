from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.db import naive_utc
from openrag.modules.auth.models import User
from openrag.modules.documents.models import DocumentAuthorityReadiness
from openrag.modules.grounding.models import GroundingCalibrationRun, GroundingPolicy
from openrag.modules.models.models import Model
from openrag.modules.tenancy.models import Organization, Workspace


def test_grounding_policy_has_bounded_rates_and_no_sensitive_payload_columns() -> None:
    table = GroundingPolicy.__table__
    column_names = set(table.columns.keys())
    assert {
        "entailment_threshold",
        "measured_false_support_rate",
        "measured_false_refusal_rate",
        "calibration_dataset_hash",
        "credential_fingerprint",
    } <= column_names
    assert not ({"secret", "prompt", "evidence_text", "provider_response"} & column_names)
    assert any(
        "entailment_threshold" in str(check.sqltext)
        for check in table.constraints
        if hasattr(check, "sqltext")
    )


def test_readiness_and_calibration_shapes_are_bounded_and_secret_free() -> None:
    readiness_columns = set(inspect(DocumentAuthorityReadiness).columns.keys())
    calibration_columns = set(inspect(GroundingCalibrationRun).columns.keys())
    assert {"generation_id", "request_digest", "expires_at", "status"} <= readiness_columns
    assert {"generation_id", "idempotency_digest", "state", "attempts"} <= calibration_columns
    sensitive = {"secret", "prompt", "evidence_text", "provider_response", "credential"}
    assert not (sensitive & readiness_columns)
    assert not (sensitive & calibration_columns)


def test_model_capabilities_default_fail_closed() -> None:
    table = Model.__table__
    assert table.c.supports_chat_completion.default.arg is False
    assert table.c.supports_structured_json.default.arg is False
    assert table.c.supports_verifier.default.arg is False
    assert table.c.provider_preset_version.nullable is True
    assert table.c.provider_preset_version.type.length == 100


async def seed_grounding_scope(
    session: AsyncSession,
    *,
    suffix: str,
) -> tuple[Organization, Workspace, User, Model]:
    organization = Organization(name=f"Grounding {suffix}")
    session.add(organization)
    await session.flush()
    workspace = Workspace(org_id=organization.id, name=f"Workspace {suffix}")
    user = User(
        org_id=organization.id,
        email=f"grounding-{suffix}@example.com",
        password_hash="x",  # noqa: S106 - inert persisted test value
    )
    model = Model(
        litellm_model_name=f"openai/{suffix}",
        display_name=f"Model {suffix}",
        provider_kind="openai",
        supports_chat_completion=True,
        supports_structured_json=True,
        supports_verifier=True,
        provider_preset_version="preset-v1",
    )
    session.add_all([workspace, user, model])
    await session.flush()
    return organization, workspace, user, model


async def make_policy(
    session: AsyncSession,
    *,
    organization: Organization,
    workspace: Workspace,
    user: User,
    model: Model,
    version: int = 1,
) -> GroundingPolicy:
    policy = GroundingPolicy(
        org_id=organization.id,
        workspace_id=workspace.id,
        policy_version=version,
        verifier_model_id=model.id,
        binding_revision="binding-v1",
        provider_preset_version="preset-v1",
        credential_fingerprint="sha256:fingerprint",
        entailment_threshold=0.9,
        calibration_dataset_version="dataset-v1",
        calibration_dataset_hash="a" * 64,
        calibration_sample_count=10,
        measured_false_support_rate=0.01,
        measured_false_refusal_rate=0.02,
        created_by=user.id,
    )
    session.add(policy)
    await session.flush()
    return policy


async def test_readiness_rejects_peer_workspace_policy(
    session: AsyncSession,
) -> None:
    organization, workspace, user, model = await seed_grounding_scope(
        session, suffix="readiness-scope"
    )
    peer = Workspace(org_id=organization.id, name="Peer Policy Workspace")
    session.add(peer)
    await session.flush()
    policy = await make_policy(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        model=model,
    )
    session.add(
        DocumentAuthorityReadiness(
            generation_id=uuid4(),
            org_id=organization.id,
            workspace_id=peer.id,
            request_digest="b" * 64,
            physical_collection="authority-v1",
            collection_alias="authority-current",
            schema_version=1,
            grounding_policy_id=policy.id,
            grounding_policy_version=policy.policy_version,
            verifier_model_id=model.id,
            calibration_hash="e" * 64,
            provider_preset_version="preset-v1",
            binding_revision="binding-v1",
            credential_fingerprint="sha256:fingerprint",
            blocker_codes=[],
            expires_at=naive_utc() + timedelta(hours=1),
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_readiness_rejects_unknown_verifier_model(
    session: AsyncSession,
) -> None:
    organization, workspace, user, model = await seed_grounding_scope(
        session, suffix="readiness-model"
    )
    policy = await make_policy(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        model=model,
    )
    session.add(
        DocumentAuthorityReadiness(
            generation_id=uuid4(),
            org_id=organization.id,
            workspace_id=workspace.id,
            request_digest="f" * 64,
            physical_collection="authority-v1",
            collection_alias="authority-current",
            schema_version=1,
            grounding_policy_id=policy.id,
            grounding_policy_version=policy.policy_version,
            verifier_model_id=uuid4(),
            calibration_hash="e" * 64,
            provider_preset_version="preset-v1",
            binding_revision="binding-v1",
            credential_fingerprint="sha256:fingerprint",
            blocker_codes=[],
            expires_at=naive_utc() + timedelta(hours=1),
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.parametrize(
    "invalid_values",
    [
        {"request_digest": "short"},
        {"schema_version": 0},
        {"blocker_codes": ["x"] * 33},
        {"status": "unknown"},
    ],
)
async def test_readiness_bounds_are_enforced_by_postgresql(
    session: AsyncSession,
    invalid_values: dict[str, object],
) -> None:
    organization, workspace, user, model = await seed_grounding_scope(
        session, suffix=f"bounds-{uuid4()}"
    )
    policy = await make_policy(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        model=model,
    )
    values: dict[str, object] = {
        "generation_id": uuid4(),
        "org_id": organization.id,
        "workspace_id": workspace.id,
        "request_digest": "c" * 64,
        "physical_collection": "authority-v1",
        "collection_alias": "authority-current",
        "schema_version": 1,
        "grounding_policy_id": policy.id,
        "grounding_policy_version": policy.policy_version,
        "verifier_model_id": model.id,
        "calibration_hash": "e" * 64,
        "provider_preset_version": "preset-v1",
        "binding_revision": "binding-v1",
        "credential_fingerprint": "sha256:fingerprint",
        "blocker_codes": [],
        "expires_at": naive_utc() + timedelta(hours=1),
    }
    values.update(invalid_values)
    session.add(DocumentAuthorityReadiness(**values))
    with pytest.raises(DBAPIError):
        await session.commit()


async def test_calibration_aggregate_counts_are_coherent(
    session: AsyncSession,
) -> None:
    organization, workspace, user, model = await seed_grounding_scope(
        session, suffix="aggregate"
    )
    policy = await make_policy(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        model=model,
    )
    session.add(
        GroundingCalibrationRun(
            generation_id=uuid4(),
            org_id=organization.id,
            workspace_id=workspace.id,
            policy_id=policy.id,
            idempotency_digest="d" * 64,
            requested_binding_revision="binding-v1",
            requested_preset_version="preset-v1",
            requested_credential_fingerprint="sha256:fingerprint",
            sample_count=2,
            supported_count=3,
            refused_count=0,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_calibration_digest_is_enforced_by_postgresql(
    session: AsyncSession,
) -> None:
    organization, workspace, user, model = await seed_grounding_scope(
        session, suffix="calibration-digest"
    )
    policy = await make_policy(
        session,
        organization=organization,
        workspace=workspace,
        user=user,
        model=model,
    )
    session.add(
        GroundingCalibrationRun(
            generation_id=uuid4(),
            org_id=organization.id,
            workspace_id=workspace.id,
            policy_id=policy.id,
            idempotency_digest="short",
            requested_binding_revision="binding-v1",
            requested_preset_version="preset-v1",
            requested_credential_fingerprint="sha256:fingerprint",
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_model_preset_version_bound_is_enforced_by_postgresql(
    session: AsyncSession,
) -> None:
    session.add(
        Model(
            litellm_model_name=f"openai/too-long-{uuid4()}",
            display_name="Too long",
            provider_kind="openai",
            provider_preset_version="x" * 101,
        )
    )
    with pytest.raises(DBAPIError):
        await session.commit()


@pytest.mark.parametrize(
    "invalid_values",
    [
        {"entailment_threshold": 1.1},
        {"calibration_dataset_hash": "short"},
        {"status": "unknown"},
        {"provider_preset_version": "x" * 101},
    ],
)
async def test_policy_bounds_are_enforced_by_postgresql(
    session: AsyncSession,
    invalid_values: dict[str, object],
) -> None:
    organization, workspace, user, model = await seed_grounding_scope(
        session, suffix=f"policy-bounds-{uuid4()}"
    )
    values: dict[str, object] = {
        "org_id": organization.id,
        "workspace_id": workspace.id,
        "policy_version": 1,
        "verifier_model_id": model.id,
        "binding_revision": "binding-v1",
        "provider_preset_version": "preset-v1",
        "credential_fingerprint": "sha256:fingerprint",
        "entailment_threshold": 0.9,
        "calibration_dataset_version": "dataset-v1",
        "calibration_dataset_hash": "a" * 64,
        "calibration_sample_count": 10,
        "status": "draft",
        "created_by": user.id,
    }
    values.update(invalid_values)
    session.add(GroundingPolicy(**values))
    with pytest.raises(DBAPIError):
        await session.commit()
