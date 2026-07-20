import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.core.db import build_engine, validate_connection_budget


def test_engine_uses_explicit_bounded_pool_configuration() -> None:
    engine = build_engine(
        "postgresql+asyncpg://openrag:openrag@127.0.0.1/openrag",
        pool_size=7,
        max_overflow=3,
        pool_timeout_seconds=4.0,
    )

    pool = engine.sync_engine.pool
    assert pool.size() == 7  # type: ignore[attr-defined]
    assert pool._max_overflow == 3  # type: ignore[attr-defined]  # noqa: SLF001
    assert pool._timeout == 4.0  # type: ignore[attr-defined]  # noqa: SLF001


def test_connection_budget_is_calculated_and_rejects_oversubscription() -> None:
    assert validate_connection_budget(
        pool_size=10,
        max_overflow=5,
        process_count=8,
        connection_budget=160,
    ) == 120

    with pytest.raises(ValueError, match="database_connection_budget_exceeded"):
        validate_connection_budget(
            pool_size=10,
            max_overflow=5,
            process_count=8,
            connection_budget=100,
        )


async def test_roundtrip(session: AsyncSession) -> None:
    result = await session.execute(text("SELECT 1"))
    assert result.scalar() == 1
