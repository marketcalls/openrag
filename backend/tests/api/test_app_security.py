import pytest
from redis.asyncio import Redis

from openrag.api.app import create_app
from openrag.core.config import Settings


def _patch_settings(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
) -> None:
    def provider() -> Settings:
        return settings

    monkeypatch.setattr("openrag.api.app.get_settings", provider)


async def test_production_disables_public_api_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, environment="production")
    _patch_settings(monkeypatch, settings)
    redis = Redis.from_url("redis://127.0.0.1:1/0")
    try:
        app = create_app(
            redis_client=redis,
            event_redis_client=redis,
        )
        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None
    finally:
        await redis.aclose()


async def test_production_requires_isolated_event_redis_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, environment="production")
    _patch_settings(monkeypatch, settings)
    redis = Redis.from_url("redis://127.0.0.1:1/0")
    try:
        with pytest.raises(
            RuntimeError,
            match="event_redis_configuration_required",
        ):
            create_app(redis_client=redis)
    finally:
        await redis.aclose()


async def test_development_schema_includes_authenticated_run_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, environment="dev")
    _patch_settings(monkeypatch, settings)
    redis = Redis.from_url("redis://127.0.0.1:1/0")
    try:
        app = create_app(
            redis_client=redis,
            event_redis_client=redis,
        )
        assert app.docs_url == "/api/docs"
        assert "/api/v1/runs/{run_id}/events" in app.openapi()["paths"]
        security = app.openapi()["paths"][
            "/api/v1/runs/{run_id}/events"
        ]["get"]["security"]
        assert security == [{"HTTPBearer": []}]
    finally:
        await redis.aclose()
