from uuid import UUID

from openrag.core.config import Settings


def test_settings_reads_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv(
        "OPENRAG_DATABASE_URL", "postgresql+asyncpg://x:y@h:5432/db"
    )
    settings = Settings(_env_file=None)
    assert settings.database_url.endswith("/db")
    assert settings.access_token_ttl_seconds == 900


def test_settings_use_openrag_database_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.database_url == (
        "postgresql+asyncpg://openrag:openrag@127.0.0.1:55432/openrag"
    )
    assert settings.redis_url == "redis://127.0.0.1:56379/0"
    assert settings.event_redis_url is None
    assert settings.event_redis_password_file is None


def test_event_transport_settings_are_distinct_and_injected(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv(
        "OPENRAG_EVENT_REDIS_URL",
        "redis://openrag@event-redis:6379/0",
    )
    monkeypatch.setenv(
        "OPENRAG_EVENT_REDIS_PASSWORD_FILE",
        "/run/secrets/event_redis_password",
    )

    settings = Settings(_env_file=None)

    assert settings.event_redis_url != settings.redis_url
    assert settings.event_redis_url == "redis://openrag@event-redis:6379/0"
    assert (
        settings.event_redis_password_file
        == "/run/secrets/event_redis_password"  # noqa: S105
    )
    assert settings.event_dispatch_batch_size == 100
    assert settings.event_dispatch_lease_seconds == 30
    assert settings.event_waitaof_timeout_ms == 5000
    assert settings.run_event_max_events == 4096
    assert settings.run_event_retention_seconds == 3600
    assert settings.run_event_block_ms == 15_000


def test_ingestion_settings_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.qdrant_url == "http://localhost:56333"
    assert settings.minio_endpoint == "http://localhost:59000"
    assert settings.minio_bucket == "openrag-documents"
    assert settings.tei_url == "http://localhost:58080"
    assert settings.embedding_backend == "tei"
    assert settings.embedding_model_id == "BAAI/bge-m3"
    assert settings.embedding_dim == 1024
    assert settings.authority_generation_id == UUID(
        "8a9848ab-6f79-5ec8-a906-a1f3c096cdb8"
    )
    assert settings.interactive_upload_mb == 10
    assert settings.upload_quarantine_dir == "./data/quarantine"
    assert settings.upload_stream_chunk_kb == 1024
    assert settings.upload_multipart_overhead_kb == 1024
    assert settings.upload_archive_max_entries == 10_000
    assert settings.upload_archive_max_uncompressed_mb == 500
    assert settings.upload_archive_max_ratio == 100
    assert settings.parser_max_pages == 1000
    assert settings.parser_max_page_pixels == 40_000_000
    assert settings.parser_render_dpi == 200
    assert settings.parser_timeout_seconds == 300
    assert settings.parser_max_blocks == 100_000
    assert settings.parser_max_output_chars == 10_000_000
    assert settings.ocr_mode == "auto"
    assert settings.ocr_languages == "english"
    assert settings.ocr_min_confidence == 0.5
    assert settings.ocr_text_score == 0.3
    assert settings.ocr_bitmap_area_threshold == 0.05
    assert settings.ocr_batch_size == 2
    assert settings.parser_hard_timeout_grace_seconds == 30
    assert settings.parser_worker_max_memory_mb == 3072
    assert settings.parser_worker_max_tasks == 25


def test_gateway_settings_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.litellm_url == "http://localhost:54000"
    assert settings.litellm_master_key == "sk-openrag-dev-master"  # noqa: S105
    assert settings.chat_context_token_budget == 8000
