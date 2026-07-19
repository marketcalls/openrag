from types import SimpleNamespace

from openrag.modules.documents.profiles import active_ingestion_profiles


def test_profile_snapshot_is_stable_bounded_and_changes_with_embedding_identity() -> None:
    base = SimpleNamespace(
        embedding_backend="litellm",
        embedding_model_id="text-embedding-3-large",
        embedding_dim=3072,
        ocr_mode="auto",
        ocr_languages="english",
        ocr_min_confidence=0.5,
        ocr_text_score=0.3,
        ocr_bitmap_area_threshold=0.05,
    )

    first = active_ingestion_profiles(base)
    repeated = active_ingestion_profiles(base)
    changed = active_ingestion_profiles(
        SimpleNamespace(**{**vars(base), "embedding_model_id": "bge-m3"})
    )

    assert first == repeated
    assert first.embedding_profile_version != changed.embedding_profile_version
    assert first.parser_profile_version == "openrag-parser/v1"
    assert first.chunking_profile_version == "openrag-page-local/v1"
    assert first.index_profile_version == "openrag-authority-hybrid/v1"
    assert all(1 <= len(value) <= 100 for value in first.as_tuple())
    assert "text-embedding-3-large" not in repr(first)


def test_ocr_profile_changes_when_ocr_policy_changes() -> None:
    values = {
        "embedding_backend": "tei",
        "embedding_model_id": "BAAI/bge-m3",
        "embedding_dim": 1024,
        "ocr_mode": "auto",
        "ocr_languages": "english",
        "ocr_min_confidence": 0.5,
        "ocr_text_score": 0.3,
        "ocr_bitmap_area_threshold": 0.05,
    }

    first = active_ingestion_profiles(SimpleNamespace(**values))
    forced = active_ingestion_profiles(
        SimpleNamespace(**{**values, "ocr_mode": "force"})
    )

    assert first.ocr_profile_version != forced.ocr_profile_version
