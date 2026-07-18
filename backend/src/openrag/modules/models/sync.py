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


def _is_empty_registry_response(response: httpx.Response) -> bool:
    """Recognize LiteLLM 1.72's empty-registry management response."""
    if response.status_code != 500:
        return False
    try:
        payload: object = response.json()
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    detail: object = payload.get("detail")
    if not isinstance(detail, dict):
        return False
    error: object = detail.get("error")
    return isinstance(error, str) and error.startswith(
        "LLM Model List not loaded in."
    )


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
    payloads = [
        {
            "model_name": model.litellm_model_name,
            "litellm_params": await _litellm_params(
                session,
                model,
                settings,
            ),
        }
        for model in enabled_models
    ]

    try:
        async with httpx.AsyncClient(
            base_url=settings.litellm_url,
            headers=headers,
            transport=transport,
            timeout=30.0,
        ) as client:
            info_response = await client.get("/v1/model/info")
            if _is_empty_registry_response(info_response):
                deployed_models: list[dict[str, Any]] = []
            else:
                info_response.raise_for_status()
                deployed_models = info_response.json().get("data", [])
            for deployed in deployed_models:
                deployed_id = deployed.get("model_info", {}).get("id")
                if deployed_id:
                    delete_response = await client.post(
                        "/model/delete",
                        json={"id": deployed_id},
                    )
                    delete_response.raise_for_status()

            for payload in payloads:
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
