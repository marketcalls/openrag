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


def test_ingestion_settings_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.qdrant_url == "http://localhost:56333"
    assert settings.minio_endpoint == "http://localhost:59000"
    assert settings.minio_bucket == "openrag-documents"
    assert settings.tei_url == "http://localhost:58080"
    assert settings.embedding_backend == "tei"
    assert settings.embedding_dim == 1024
    assert settings.interactive_upload_mb == 10
    assert settings.upload_quarantine_dir == "./data/quarantine"
    assert settings.upload_stream_chunk_kb == 1024
    assert settings.upload_archive_max_entries == 10_000
    assert settings.upload_archive_max_uncompressed_mb == 500
    assert settings.upload_archive_max_ratio == 100


def test_gateway_settings_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.litellm_url == "http://localhost:54000"
    assert settings.litellm_master_key == "sk-openrag-dev-master"  # noqa: S105
    assert settings.chat_context_token_budget == 8000
