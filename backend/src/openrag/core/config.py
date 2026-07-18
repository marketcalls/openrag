from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPENRAG_",
        env_file=".env",
        extra="ignore",
    )

    database_url: str = (
        "postgresql+asyncpg://openrag:openrag@localhost:5432/openrag"
    )
    redis_url: str = "redis://localhost:6379/0"
    environment: str = "dev"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 1_209_600


@lru_cache
def get_settings() -> Settings:
    return Settings()
