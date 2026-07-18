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


def test_ingestion_settings_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.qdrant_url == "http://localhost:56333"
    assert settings.minio_endpoint == "http://localhost:59000"
    assert settings.minio_bucket == "openrag-documents"
    assert settings.tei_url == "http://localhost:58080"
    assert settings.embedding_backend == "tei"
    assert settings.embedding_dim == 1024
    assert settings.interactive_upload_mb == 10
