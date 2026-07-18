"""Idempotent registry replay to LiteLLM's management API.

The gateway database is treated as replaceable state. Each replay removes the
currently deployed models and recreates the enabled OpenRAG registry entries.
This module is the only sanctioned caller of the provider-secret decryption
path.
"""

from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.config import Settings
from openrag.core.errors import NotFoundError, UpstreamError
from openrag.modules.models.models import Model
from openrag.modules.models.service import list_enabled_models, list_models
from openrag.modules.secrets import service as secrets_service


async def _litellm_params(
    session: AsyncSession,
    model: Model,
    settings: Settings,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if model.provider_kind == "ollama":
        params["model"] = f"ollama/{model.litellm_model_name}"
        params["api_base"] = model.base_url
    else:
        params["model"] = f"openai/{model.litellm_model_name}"
        if model.provider_kind == "openai_compatible":
            params["api_base"] = model.base_url

    try:
        params["api_key"] = await secrets_service._get_secret_decrypted(  # noqa: SLF001
            session,
            name=f"model:{model.id}",
            settings=settings,
        )
    except NotFoundError:
        pass
    return params


async def sync_models_to_litellm(
    session: AsyncSession,
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    """Replace LiteLLM's deployed set and return the enabled-model count."""
    enabled_models = await list_enabled_models(session)
    all_models = await list_models(session)
    headers = {"Authorization": f"Bearer {settings.litellm_master_key}"}

    try:
        async with httpx.AsyncClient(
            base_url=settings.litellm_url,
            headers=headers,
            transport=transport,
            timeout=30.0,
        ) as client:
            info_response = await client.get("/v1/model/info")
            info_response.raise_for_status()
            for deployed in info_response.json().get("data", []):
                deployed_id = deployed.get("model_info", {}).get("id")
                if deployed_id:
                    delete_response = await client.post(
                        "/model/delete",
                        json={"id": deployed_id},
                    )
                    delete_response.raise_for_status()

            for model in enabled_models:
                payload = {
                    "model_name": model.litellm_model_name,
                    "litellm_params": await _litellm_params(
                        session,
                        model,
                        settings,
                    ),
                }
                create_response = await client.post(
                    "/model/new",
                    json=payload,
                )
                create_response.raise_for_status()
    except httpx.HTTPError as exc:
        for model in all_models:
            model.sync_status = "error"
        await session.commit()
        raise UpstreamError("LiteLLM sync failed") from exc

    for model in all_models:
        model.sync_status = "synced"
    await session.commit()
    return len(enabled_models)
