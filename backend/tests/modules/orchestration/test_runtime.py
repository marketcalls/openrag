from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.core.config import Settings
from openrag.modules.models.models import Model
from openrag.modules.orchestration import runtime as orchestration_runtime
from openrag.modules.orchestration.model_gateway import ModelRuntime
from openrag.modules.tenancy.authorization import AuthorizationSnapshot
from openrag.modules.tenancy.context import TenantContext


def model(*, measured: bool) -> Model:
    return Model(
        id=uuid4(),
        litellm_model_name="gpt-5-mini",
        display_name="GPT-5 Mini",
        provider_kind="openai",
        enabled=True,
        probe_status="passed" if measured else "pending",
        supports_chat_completion=measured,
        supports_streaming=measured,
        supports_structured_json=measured,
    )


def context() -> tuple[TenantContext, object]:
    user_id = uuid4()
    org_id = uuid4()
    workspace_id = uuid4()
    return (
        TenantContext(
            user_id=user_id,
            org_id=org_id,
            authorization=AuthorizationSnapshot(
                user_id=user_id,
                org_id=org_id,
                is_platform_superadmin=False,
                org_permissions=frozenset({"chat.use"}),
                workspace_permissions={},
                workspace_ids=frozenset({workspace_id}),
            ),
        ),
        workspace_id,
    )


@pytest.mark.parametrize("measured", [True, False])
async def test_model_execution_gates_analytics_on_measured_structured_output(
    monkeypatch: pytest.MonkeyPatch,
    measured: bool,
) -> None:
    resolved = ModelRuntime(
        litellm_model="openai/gpt-5-mini",
        api_key="secret",
        api_base=None,
        max_output_tokens=4_096,
    )

    async def fake_runtime(*args: object, **kwargs: object) -> ModelRuntime:
        del args, kwargs
        return resolved

    monkeypatch.setattr(orchestration_runtime, "resolve_model_runtime", fake_runtime)
    tenant, workspace_id = context()
    execution = await orchestration_runtime.create_model_execution(
        cast(AsyncSession, object()),
        model(measured=measured),
        Settings(_env_file=None),
        session_factory=cast(async_sessionmaker[AsyncSession], object()),
        context=tenant,
        workspace_id=workspace_id,  # type: ignore[arg-type]
        document_authority_enabled=False,
    )

    assert (execution.analytics_composer is not None) is measured
