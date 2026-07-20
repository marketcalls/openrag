from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk


class Model(UUIDPk, Base):
    __tablename__ = "models"
    __table_args__ = (
        CheckConstraint(
            "default_reasoning_effort IN ('off','low','medium','high') "
            "AND (supports_reasoning OR default_reasoning_effort = 'off')",
            name="ck_models_default_reasoning_effort",
        ),
    )

    litellm_model_name: Mapped[str] = mapped_column(unique=True)
    display_name: Mapped[str]
    provider_kind: Mapped[str]
    base_url: Mapped[str | None] = mapped_column(default=None)
    enabled: Mapped[bool] = mapped_column(default=True)
    supports_chat_completion: Mapped[bool] = mapped_column(default=False)
    supports_structured_json: Mapped[bool] = mapped_column(default=False)
    supports_verifier: Mapped[bool] = mapped_column(default=False)
    supports_reasoning: Mapped[bool] = mapped_column(default=False)
    default_reasoning_effort: Mapped[str] = mapped_column(
        String(16),
        default="off",
    )
    provider_preset_version: Mapped[str | None] = mapped_column(String(100), default=None)
