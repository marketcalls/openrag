from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def naive_utc() -> datetime:
    """Return naive UTC for PostgreSQL TIMESTAMP WITHOUT TIME ZONE columns."""

    return datetime.now(UTC).replace(tzinfo=None)


class UUIDPk:
    """UUID primary key and creation timestamp shared by persisted entities."""

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(default=naive_utc)


def build_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
