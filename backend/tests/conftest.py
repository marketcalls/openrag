from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from testcontainers.minio import MinioContainer
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from openrag.api.app import create_app
from openrag.core.db import Base, build_engine, build_session_factory
from openrag.core.storage import ObjectStorage
from openrag.modules.auth.models import User
from openrag.modules.auth.passwords import hash_password
from openrag.modules.tenancy.models import Organization


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine") as postgres:
        yield postgres.get_connection_url().replace("psycopg2", "asyncpg")


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer("redis:7-alpine") as redis:
        yield (
            f"redis://{redis.get_container_host_ip()}:"
            f"{redis.get_exposed_port(6379)}/0"
        )


@pytest.fixture
async def redis_client(redis_url: str) -> AsyncIterator[Redis]:
    redis = Redis.from_url(redis_url)
    await redis.flushdb()
    yield redis
    await redis.aclose()


@pytest.fixture(scope="session")
def minio_config() -> Iterator[dict[str, str]]:
    with MinioContainer() as minio:
        config = minio.get_config()
        yield {
            "endpoint": f"http://{config['endpoint']}",
            "access_key": config["access_key"],
            "secret_key": config["secret_key"],
        }


@pytest.fixture
async def storage(minio_config: dict[str, str]) -> ObjectStorage:
    object_storage = ObjectStorage(
        endpoint_url=minio_config["endpoint"],
        access_key=minio_config["access_key"],
        secret_key=minio_config["secret_key"],
        bucket="openrag-test",
    )
    await object_storage.ensure_bucket()
    return object_storage


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    database_engine = build_engine(pg_url)
    async with database_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield database_engine
    async with database_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await database_engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = build_session_factory(engine)
    async with factory() as database_session:
        yield database_session


@pytest.fixture
async def client(
    engine: AsyncEngine,
    redis_client: Redis,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as http_client:
        yield http_client


@pytest.fixture
async def seeded_user(session: AsyncSession) -> User:
    organization = Organization(name="Acme")
    session.add(organization)
    await session.flush()
    user = User(
        org_id=organization.id,
        email="a@acme.com",
        password_hash=hash_password("pw123456"),
        role="admin",
    )
    session.add(user)
    await session.commit()
    return user
