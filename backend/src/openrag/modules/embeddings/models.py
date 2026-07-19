"""Persisted immutable embedding configuration identities."""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk


class EmbeddingProfile(UUIDPk, Base):
    __tablename__ = "embedding_profiles"
    __table_args__ = (
        UniqueConstraint("name_key", name="uq_embedding_profiles_name_key"),
        CheckConstraint(
            "provider_kind IN ('litellm','tei','hash')",
            name="ck_embedding_profiles_provider_kind",
        ),
        CheckConstraint(
            "dimension BETWEEN 1 AND 32768",
            name="ck_embedding_profiles_dimension",
        ),
        CheckConstraint(
            "max_input_tokens BETWEEN 1 AND 2000000",
            name="ck_embedding_profiles_max_input_tokens",
        ),
        CheckConstraint(
            "batch_size BETWEEN 1 AND 1024",
            name="ck_embedding_profiles_batch_size",
        ),
        CheckConstraint(
            "config_digest ~ '^[0-9a-f]{64}$'",
            name="ck_embedding_profiles_config_digest",
        ),
    )

    name: Mapped[str] = mapped_column(String(120))
    name_key: Mapped[str] = mapped_column(String(120))
    provider_kind: Mapped[str] = mapped_column(String(32))
    model_name: Mapped[str] = mapped_column(String(200))
    dimension: Mapped[int]
    max_input_tokens: Mapped[int]
    batch_size: Mapped[int]
    config_digest: Mapped[str] = mapped_column(String(64), unique=True)
    enabled: Mapped[bool] = mapped_column(default=True, index=True)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
