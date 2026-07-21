"""Searchable, credential-free model presets imported from the RAGFlow catalog."""

import json
from functools import lru_cache
from pathlib import Path
from typing import cast

from openrag.modules.models.schemas import (
    CatalogCapability,
    ModelCatalogItemOut,
    ModelCatalogPageOut,
    ProviderKind,
)

_CATALOG_PATH = Path(__file__).with_name("catalog.json")
_CAPABILITIES = frozenset(
    {"asr", "chat", "doc_parse", "embedding", "ocr", "rerank", "tts", "vision"}
)
_NATIVE_LITELLM_PREFIX = {
    "Anthropic": "anthropic",
    "Azure-OpenAI": "azure",
    "Bedrock": "bedrock",
    "Cohere": "cohere",
    "CometAPI": "cometapi",
    "DeepInfra": "deepinfra",
    "DeepSeek": "deepseek",
    "Gemini": "gemini",
    "Groq": "groq",
    "HuggingFace": "huggingface",
    "Jina": "jina_ai",
    "LM-Studio": "lm_studio",
    "MiniMax": "minimax",
    "Mistral": "mistral",
    "ModelScope": "modelscope",
    "Moonshot": "moonshot",
    "NVIDIA": "nvidia_nim",
    "NovitaAI": "novita",
    "Ollama": "ollama",
    "OpenAI": "openai",
    "OpenRouter": "openrouter",
    "Perplexity": "perplexity",
    "Replicate": "replicate",
    "Tencent Hunyuan": "tencent",
    "TogetherAI": "together_ai",
    "Tongyi-Qianwen": "dashscope",
    "VLLM": "vllm",
    "VolcEngine": "volcengine",
    "Voyage AI": "voyage",
    "xAI": "xai",
    "Xiaomi": "xiaomi_mimo",
    "Xinference": "xinference",
    "ZHIPU-AI": "zai",
}


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


@lru_cache(maxsize=1)
def _catalog() -> tuple[ModelCatalogItemOut, ...]:
    decoded: object = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    if not isinstance(decoded, list):
        raise RuntimeError("model_catalog_invalid")
    items: list[ModelCatalogItemOut] = []
    for raw in decoded:
        if not isinstance(raw, dict):
            raise RuntimeError("model_catalog_invalid")
        provider = _optional_string(raw.get("provider"))
        model_id = _optional_string(raw.get("model_id"))
        raw_capabilities = raw.get("capabilities")
        if (
            provider is None
            or model_id is None
            or not isinstance(raw_capabilities, list)
            or any(value not in _CAPABILITIES for value in raw_capabilities)
        ):
            raise RuntimeError("model_catalog_invalid")
        capabilities = cast(list[CatalogCapability], raw_capabilities)
        native_prefix = _NATIVE_LITELLM_PREFIX.get(provider)
        if native_prefix is not None:
            provider_kind: ProviderKind = "litellm"
            litellm_model_name = (
                model_id
                if model_id.startswith(f"{native_prefix}/")
                else f"{native_prefix}/{model_id}"
            )
            suggested_base_url = None
        else:
            provider_kind = "openai_compatible"
            litellm_model_name = model_id
            suggested_base_url = _optional_string(raw.get("base_url"))
        items.append(
            ModelCatalogItemOut(
                provider=provider,
                model_id=model_id,
                capabilities=capabilities,
                max_tokens=_optional_positive_int(raw.get("max_tokens")),
                provider_kind=provider_kind,
                litellm_model_name=litellm_model_name,
                suggested_base_url=suggested_base_url,
            )
        )
    return tuple(
        sorted(
            items,
            key=lambda item: (item.provider.casefold(), item.model_id.casefold()),
        )
    )


def search_catalog(
    *,
    capability: CatalogCapability | None = None,
    query: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> ModelCatalogPageOut:
    normalized = query.strip().casefold() if query else ""
    matching = [
        item
        for item in _catalog()
        if (capability is None or capability in item.capabilities)
        and (
            not normalized
            or normalized in item.provider.casefold()
            or normalized in item.model_id.casefold()
            or normalized in item.litellm_model_name.casefold()
        )
    ]
    return ModelCatalogPageOut(
        items=matching[offset : offset + limit],
        total=len(matching),
        offset=offset,
        limit=limit,
    )
