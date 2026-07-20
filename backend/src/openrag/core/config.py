from functools import lru_cache
from typing import Literal
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPENRAG_",
        env_file=".env",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://openrag:openrag@127.0.0.1:55432/openrag"
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=5, ge=0, le=100)
    database_pool_timeout_seconds: float = Field(default=5.0, ge=0.1, le=60)
    database_process_count: int = Field(default=8, ge=1, le=1000)
    database_connection_budget: int = Field(default=160, ge=1, le=100_000)
    runtime_metric_interval_seconds: float = Field(default=5.0, ge=0.1, le=300)
    redis_url: str = "redis://127.0.0.1:56379/0"
    event_redis_url: str | None = None
    event_redis_password_file: str | None = None
    event_dispatch_batch_size: int = Field(default=100, ge=1, le=100)
    event_dispatch_lease_seconds: int = Field(default=30, ge=5, le=300)
    document_stage_lease_seconds: int = Field(default=120, ge=30, le=600)
    document_stage_soft_time_limit_seconds: int = Field(
        default=900,
        ge=60,
        le=7200,
    )
    event_waitaof_timeout_ms: int = Field(default=5000, ge=100, le=30_000)
    run_event_max_events: int = Field(default=4096, ge=1, le=65_536)
    run_event_retention_seconds: int = Field(
        default=3600,
        ge=60,
        le=604_800,
    )
    run_event_block_ms: int = Field(default=15_000, ge=100, le=60_000)
    run_lease_seconds: int = Field(default=60, ge=15, le=600)
    evaluation_lease_seconds: int = Field(default=300, ge=30, le=600)
    environment: str = "dev"
    release: str = Field(
        default="dev",
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}$",
    )
    otel_endpoint: str | None = None
    otel_service_name: str = Field(
        default="openrag",
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$",
    )
    otel_insecure: bool = True
    otel_trace_sample_ratio: float = Field(default=0.1, ge=0, le=1)
    otel_export_timeout_ms: int = Field(default=5000, ge=100, le=30_000)
    otel_batch_delay_ms: int = Field(default=5000, ge=100, le=60_000)
    otel_batch_queue_size: int = Field(default=2048, ge=128, le=65_536)
    otel_batch_size: int = Field(default=512, ge=1, le=8192)
    otel_metric_interval_ms: int = Field(default=60_000, ge=1000, le=300_000)
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
    embedding_model_id: str = Field(default="BAAI/bge-m3", min_length=1, max_length=200)
    embedding_dim: int = 1024
    authority_generation_id: UUID = UUID("8a9848ab-6f79-5ec8-a906-a1f3c096cdb8")
    max_upload_mb: int = 100
    upload_quarantine_dir: str = "./data/quarantine"
    upload_stream_chunk_kb: int = Field(default=1024, ge=64, le=4096)
    upload_multipart_overhead_kb: int = Field(default=1024, ge=64, le=8192)
    upload_archive_max_entries: int = Field(default=10_000, ge=1, le=100_000)
    upload_archive_max_uncompressed_mb: int = Field(default=500, ge=1, le=5000)
    upload_archive_max_ratio: int = Field(default=100, ge=1, le=1000)
    parser_max_pages: int = Field(default=1000, ge=1, le=10_000)
    parser_max_page_pixels: int = Field(default=40_000_000, ge=1_000_000, le=250_000_000)
    parser_render_dpi: int = Field(default=200, ge=72, le=600)
    parser_timeout_seconds: int = Field(default=300, ge=10, le=3600)
    parser_hard_timeout_grace_seconds: int = Field(default=30, ge=5, le=300)
    parser_worker_max_memory_mb: int = Field(default=3072, ge=512, le=16_384)
    parser_worker_max_tasks: int = Field(default=25, ge=1, le=1000)
    parser_max_blocks: int = Field(default=100_000, ge=1, le=1_000_000)
    parser_max_output_chars: int = Field(default=10_000_000, ge=1000, le=100_000_000)
    ocr_mode: Literal["auto", "force", "disabled"] = "auto"
    ocr_languages: str = "english"
    ocr_min_confidence: float = Field(default=0.5, ge=0, le=1)
    ocr_text_score: float = Field(default=0.3, ge=0, le=1)
    ocr_bitmap_area_threshold: float = Field(default=0.05, ge=0, le=1)
    ocr_batch_size: int = Field(default=2, ge=1, le=16)
    interactive_upload_mb: int = 10
    # Keep direct v2 dispatch disabled until every legacy worker is drained.
    ingest_revision_protocol_v2_enabled: bool = False
    stale_ingest_recovery_seconds: int = 900
    chat_context_token_budget: int = 8000
    chat_max_output_tokens: int = Field(default=2048, ge=1, le=32_768)
    summary_trigger_tokens: int = Field(default=3000, ge=256, le=32_768)
    summary_source_token_budget: int = Field(default=12_000, ge=1024, le=131_072)
    summary_target_token_budget: int = Field(default=800, ge=128, le=4096)
    summary_lease_seconds: int = Field(default=120, ge=30, le=600)


@lru_cache
def get_settings() -> Settings:
    return Settings()
