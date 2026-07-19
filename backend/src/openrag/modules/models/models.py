from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk


class Model(UUIDPk, Base):
    __tablename__ = "models"

    litellm_model_name: Mapped[str] = mapped_column(unique=True)
    display_name: Mapped[str]
    provider_kind: Mapped[str]
    base_url: Mapped[str | None] = mapped_column(default=None)
    enabled: Mapped[bool] = mapped_column(default=True)
    sync_status: Mapped[str] = mapped_column(default="pending")
    supports_chat_completion: Mapped[bool] = mapped_column(default=False)
    supports_structured_json: Mapped[bool] = mapped_column(default=False)
    supports_verifier: Mapped[bool] = mapped_column(default=False)
    provider_preset_version: Mapped[str | None] = mapped_column(String(100), default=None)
