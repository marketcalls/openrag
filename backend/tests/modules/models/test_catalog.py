from openrag.modules.models.catalog import search_catalog


def test_catalog_snapshot_contains_every_ragflow_provider_entry() -> None:
    page = search_catalog(limit=1_000)

    assert page.total == 602
    assert len(page.items) == 602


def test_catalog_maps_native_litellm_ids_without_guessing_capabilities() -> None:
    page = search_catalog(capability="chat", query="claude-sonnet-4-6", limit=100)
    anthropic = next(item for item in page.items if item.provider == "Anthropic")

    assert anthropic.litellm_model_name == "anthropic/claude-sonnet-4-6"
    assert anthropic.provider_kind == "litellm"
    assert "chat" in anthropic.capabilities
    assert anthropic.suggested_base_url is None


def test_catalog_keeps_openai_compatible_fallback_explicit() -> None:
    page = search_catalog(capability="embedding", query="bge-m3", limit=100)
    siliconflow = next(item for item in page.items if item.provider == "SILICONFLOW")

    assert siliconflow.provider_kind == "openai_compatible"
    assert siliconflow.litellm_model_name == "BAAI/bge-m3"
    assert siliconflow.suggested_base_url is not None
