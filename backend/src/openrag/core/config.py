from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPENRAG_",
        env_file=".env",
        extra="ignore",
    )

    database_url: str = (
        "postgresql+asyncpg://openrag:openrag@127.0.0.1:55432/openrag"
    )
    redis_url: str = "redis://127.0.0.1:56379/0"
    environment: str = "dev"
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
    interactive_upload_mb: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
