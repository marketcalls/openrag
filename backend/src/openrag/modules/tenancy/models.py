from sqlalchemy.orm import Mapped, mapped_column

from openrag.core.db import Base, UUIDPk


class Organization(UUIDPk, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(unique=True)
