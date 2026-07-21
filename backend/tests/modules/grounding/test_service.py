from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.auth.models import User
from openrag.modules.grounding.service import provision_default_grounding_policy
from openrag.modules.models.models import Model, ModelProbe
from openrag.modules.tenancy.models import Organization, Workspace


async def test_default_policy_binds_latest_measured_verifier_idempotently(
    session: AsyncSession,
) -> None:
    organization = Organization(name="Grounded Co")
    session.add(organization)
    await session.flush()
    user = User(
        org_id=organization.id,
        email="grounding@example.com",
        password_hash="inert-test-hash",  # noqa: S106
    )
    workspace = Workspace(
        org_id=organization.id,
        name="Safety",
        document_authority_enabled=True,
    )
    verifier = Model(
        litellm_model_name="openai/gpt-4o-mini-grounding-test",
        display_name="Verifier",
        provider_kind="openai",
        enabled=True,
        supports_chat_completion=True,
        supports_streaming=True,
        supports_structured_json=True,
        supports_verifier=True,
        supports_tools=False,
        supports_vision=False,
        supports_reasoning=False,
        probe_status="passed",
        probe_revision=3,
    )
    session.add_all([user, workspace, verifier])
    await session.flush()
    probe = ModelProbe(
        model_id=verifier.id,
        requested_by=user.id,
        revision=3,
        configuration_fingerprint="a" * 64,
        status="passed",
        attempts=1,
        supports_chat_completion=True,
        supports_streaming=True,
        supports_structured_json=True,
    )
    session.add(probe)
    await session.flush()

    first = await provision_default_grounding_policy(
        session,
        org_id=organization.id,
        workspace_id=workspace.id,
        created_by=user.id,
    )
    second = await provision_default_grounding_policy(
        session,
        org_id=organization.id,
        workspace_id=workspace.id,
        created_by=user.id,
    )

    assert first is not None
    assert second is first
    assert first.verifier_model_id == verifier.id
    assert first.binding_revision == "model-probe-r3"
    assert first.credential_fingerprint == "a" * 64
    assert first.status == "active"
    assert first.calibration_sample_count == 0


async def test_default_policy_is_not_fabricated_without_a_measured_verifier(
    session: AsyncSession,
) -> None:
    organization = Organization(name="No Verifier Co")
    session.add(organization)
    await session.flush()
    user = User(
        org_id=organization.id,
        email="no-verifier@example.com",
        password_hash="inert-test-hash",  # noqa: S106
    )
    workspace = Workspace(org_id=organization.id, name="Private")
    session.add_all([user, workspace])
    await session.flush()

    policy = await provision_default_grounding_policy(
        session,
        org_id=organization.id,
        workspace_id=workspace.id,
        created_by=user.id,
    )

    assert policy is None
