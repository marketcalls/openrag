from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DatabaseEngineSettings(Protocol):
    database_url: str
    database_pool_size: int
    database_max_overflow: int
    database_pool_timeout_seconds: float
    database_process_count: int
    database_connection_budget: int


def naive_utc() -> datetime:
    """Return naive UTC for PostgreSQL TIMESTAMP WITHOUT TIME ZONE columns."""

    return datetime.now(UTC).replace(tzinfo=None)


class UUIDPk:
    """UUID primary key and creation timestamp shared by persisted entities."""

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(default=naive_utc)


def validate_connection_budget(
    *,
    pool_size: int,
    max_overflow: int,
    process_count: int,
    connection_budget: int,
) -> int:
    maximum_connections = (pool_size + max_overflow) * process_count
    if maximum_connections > connection_budget:
        raise ValueError(
            "database_connection_budget_exceeded: "
            f"configured={maximum_connections} budget={connection_budget}"
        )
    return maximum_connections


def build_engine(
    url: str,
    *,
    pool_size: int = 10,
    max_overflow: int = 5,
    pool_timeout_seconds: float = 5.0,
) -> AsyncEngine:
    return create_async_engine(
        url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout_seconds,
    )


def build_configured_engine(settings: DatabaseEngineSettings) -> AsyncEngine:
    validate_connection_budget(
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        process_count=settings.database_process_count,
        connection_budget=settings.database_connection_budget,
    )
    return build_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout_seconds=settings.database_pool_timeout_seconds,
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session
