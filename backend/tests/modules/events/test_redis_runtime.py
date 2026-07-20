from pathlib import Path

import pytest

from openrag.core.config import Settings
from openrag.modules.events.redis_runtime import (
    EventRedisConfigurationError,
    build_event_redis,
    read_event_redis_password,
)


def _settings(path: Path | None) -> Settings:
    return Settings(
        _env_file=None,
        event_redis_url="redis://openrag@event-redis:6379/0",
        event_redis_password_file=str(path) if path is not None else None,
    )


def test_event_password_is_read_from_bounded_secret_file(tmp_path: Path) -> None:
    path = tmp_path / "event-password"
    path.write_text("correct-horse-battery-staple\n", encoding="utf-8")

    assert read_event_redis_password(_settings(path)) == (
        "correct-horse-battery-staple"
    )


@pytest.mark.parametrize("value", ["short", "x" * 513])
def test_event_password_rejects_unsafe_lengths(
    tmp_path: Path,
    value: str,
) -> None:
    path = tmp_path / "event-password"
    path.write_text(value, encoding="utf-8")

    with pytest.raises(
        EventRedisConfigurationError,
        match="event_redis_password_invalid",
    ):
        read_event_redis_password(_settings(path))


def test_event_password_file_is_required() -> None:
    with pytest.raises(
        EventRedisConfigurationError,
        match="event_redis_password_file_required",
    ):
        read_event_redis_password(_settings(None))


def test_event_redis_url_is_required(tmp_path: Path) -> None:
    path = tmp_path / "event-password"
    path.write_text("correct-horse-battery-staple", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        event_redis_url=None,
        event_redis_password_file=str(path),
    )

    with pytest.raises(
        EventRedisConfigurationError,
        match="event_redis_url_required",
    ):
        build_event_redis(settings)
