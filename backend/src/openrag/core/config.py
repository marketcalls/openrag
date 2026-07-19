from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPENRAG_",
        env_file=".env",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://openrag:openrag@127.0.0.1:55432/openrag"
    redis_url: str = "redis://127.0.0.1:56379/0"
    event_redis_url: str | None = None
    event_redis_password_file: str | None = None
    event_dispatch_batch_size: int = Field(default=100, ge=1, le=100)
    event_dispatch_lease_seconds: int = Field(default=30, ge=5, le=300)
    event_waitaof_timeout_ms: int = Field(default=5000, ge=100, le=30_000)
    environment: str = "dev"
    kek_file: str = "./data/openrag_kek"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 1_209_600
    qdrant_url: str = "http://localhost:56333"
    minio_endpoint: str = "http://localhost:59000"
    minio_access_key: str = "openrag"
    minio_secret_key: str = "openrag123"  # noqa: S105 - local compose credential
    minio_bucket: str = "openrag-documents"
    tei_url: str = "http://localhost:58080"
    embedding_backend: str = "tei"
    embedding_dim: int = 1024
    max_upload_mb: int = 100
    upload_quarantine_dir: str = "./data/quarantine"
    upload_stream_chunk_kb: int = Field(default=1024, ge=64, le=4096)
    upload_multipart_overhead_kb: int = Field(default=1024, ge=64, le=8192)
    upload_archive_max_entries: int = Field(default=10_000, ge=1, le=100_000)
    upload_archive_max_uncompressed_mb: int = Field(default=500, ge=1, le=5000)
    upload_archive_max_ratio: int = Field(default=100, ge=1, le=1000)
    interactive_upload_mb: int = 10
    # Keep direct v2 dispatch disabled until every legacy worker is drained.
    ingest_revision_protocol_v2_enabled: bool = False
    stale_ingest_recovery_seconds: int = 900
    litellm_url: str = "http://localhost:54000"
    litellm_master_key: str = "sk-openrag-dev-master"  # noqa: S105
    chat_context_token_budget: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
