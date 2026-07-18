from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from testcontainers.minio import MinioContainer
from testcontainers.postgres import PostgresContainer
from testcontainers.qdrant import QdrantContainer
from testcontainers.redis import RedisContainer

from openrag.api.app import create_app
from openrag.core.config import Settings, get_settings
from openrag.core.db import Base, build_engine, build_session_factory
from openrag.core.storage import ObjectStorage
from openrag.modules.auth.models import User
from openrag.modules.auth.passwords import hash_password
from openrag.modules.chat.llm import LLMDelta, LLMUsage
from openrag.modules.documents.models import Document
from openrag.modules.retrieval.client import COLLECTION, get_qdrant
from openrag.modules.retrieval.embeddings import get_dense_embedder
from openrag.modules.retrieval.service import (
    RetrievalResult,
    RetrievedChunk,
    ensure_collection,
)
from openrag.modules.secrets.crypto import ensure_kek
from openrag.modules.tenancy.context import TenantContext
from openrag.modules.tenancy.models import (
    Organization,
    Workspace,
    WorkspaceMember,
)


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


@pytest.fixture(scope="session")
def qdrant_url() -> Iterator[str]:
    with QdrantContainer("qdrant/qdrant:v1.18.0") as qdrant:
        yield (
            f"http://{qdrant.get_container_host_ip()}:"
            f"{qdrant.get_exposed_port(6333)}"
        )


def clear_ambient_caches() -> None:
    get_settings.cache_clear()
    get_qdrant.cache_clear()
    get_dense_embedder.cache_clear()


def stub_litellm_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1/model/info":
        return httpx.Response(200, json={"data": []})
    return httpx.Response(200, json={})


class FakeStreamer:
    def __init__(self, deltas: list[str] | None = None) -> None:
        self.deltas = (
            deltas
            if deltas is not None
            else ["Revenue was 12M ", "[1]."]
        )
        self.calls: list[dict[str, object]] = []

    async def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[LLMDelta | LLMUsage]:
        self.calls.append({"model": model, "messages": messages})
        for delta in self.deltas:
            yield LLMDelta(delta)
        yield LLMUsage(prompt_tokens=42, completion_tokens=7)


class FakeRetriever:
    def __init__(self, document_id: UUID, no_answer: bool = False) -> None:
        self.document_id = document_id
        self.no_answer = no_answer

    async def __call__(
        self,
        session: AsyncSession,
        context: TenantContext,
        workspace_id: UUID,
        query: str,
        top_k: int = 8,
    ) -> RetrievalResult:
        return RetrievalResult(
            chunks=[
                RetrievedChunk(
                    document_id=self.document_id,
                    page=3,
                    chunk_index=0,
                    text="Revenue was 12M.",
                    score=0.91,
                ),
                RetrievedChunk(
                    document_id=self.document_id,
                    page=5,
                    chunk_index=2,
                    text="Costs were 4M.",
                    score=0.55,
                ),
            ],
            no_answer=self.no_answer,
        )


@pytest.fixture
def stack_env(
    pg_url: str,
    qdrant_url: str,
    minio_config: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.setenv("OPENRAG_DATABASE_URL", pg_url)
    monkeypatch.setenv("OPENRAG_QDRANT_URL", qdrant_url)
    monkeypatch.setenv("OPENRAG_MINIO_ENDPOINT", minio_config["endpoint"])
    monkeypatch.setenv("OPENRAG_MINIO_ACCESS_KEY", minio_config["access_key"])
    monkeypatch.setenv("OPENRAG_MINIO_SECRET_KEY", minio_config["secret_key"])
    monkeypatch.setenv("OPENRAG_MINIO_BUCKET", "openrag-test")
    monkeypatch.setenv("OPENRAG_EMBEDDING_BACKEND", "hash")
    clear_ambient_caches()
    yield
    clear_ambient_caches()


@pytest.fixture
async def qdrant_collection(stack_env: None) -> None:
    client = get_qdrant()
    if await client.collection_exists(COLLECTION):
        await client.delete_collection(COLLECTION)
    await ensure_collection()


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
def kek_file(tmp_path_factory: pytest.TempPathFactory) -> str:
    path = tmp_path_factory.mktemp("kek") / "openrag_kek"
    ensure_kek(str(path))
    return str(path)


@pytest.fixture
def test_settings(kek_file: str) -> Settings:
    return Settings(_env_file=None, kek_file=kek_file)


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
    test_settings: Settings,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(stub_litellm_handler),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
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


@pytest.fixture
async def seeded_superadmin(session: AsyncSession) -> User:
    organization = Organization(name="Platform")
    session.add(organization)
    await session.flush()
    user = User(
        org_id=organization.id,
        email="root@platform.example.com",
        password_hash=hash_password("pw123456"),
        role="superadmin",
    )
    session.add(user)
    await session.commit()
    return user


@pytest.fixture
async def chat_env(
    session: AsyncSession,
    seeded_user: User,
) -> dict[str, object]:
    workspace = Workspace(org_id=seeded_user.org_id, name="Chat Workspace")
    session.add(workspace)
    await session.flush()
    session.add(
        WorkspaceMember(
            workspace_id=workspace.id,
            user_id=seeded_user.id,
        )
    )
    document = Document(
        org_id=seeded_user.org_id,
        workspace_id=workspace.id,
        filename="report.pdf",
        mime="application/pdf",
        size_bytes=10,
        content_hash="chat-test-document",
        status="indexed",
        storage_key="chat-test/report.pdf",
        created_by=seeded_user.id,
    )
    session.add(document)
    await session.commit()
    return {"workspace": workspace, "document": document}
