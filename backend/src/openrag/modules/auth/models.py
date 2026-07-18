from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk


class User(UUIDPk, Base):
    __tablename__ = "users"

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    email: Mapped[str] = mapped_column(unique=True, index=True)
    password_hash: Mapped[str]
    role: Mapped[str]
    active: Mapped[bool] = mapped_column(default=True)
