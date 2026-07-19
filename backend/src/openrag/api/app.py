from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from openrag.api.middleware.request_body_limit import UploadBodyLimitMiddleware
from openrag.api.routes.admin_secrets import router as admin_secrets_router
from openrag.api.routes.auth import router as auth_router
from openrag.api.routes.chats import router as chats_router
from openrag.api.routes.documents import router as documents_router
from openrag.api.routes.embedding_profiles import router as embedding_profiles_router
from openrag.api.routes.health import router as health_router
from openrag.api.routes.models import router as models_router
from openrag.api.routes.roles import router as roles_router
from openrag.api.routes.search import router as search_router
from openrag.api.routes.users import router as users_router
from openrag.api.routes.workspaces import router as workspaces_router
from openrag.core.config import get_settings
from openrag.core.db import build_engine, build_session_factory
from openrag.core.errors import OpenRAGError
from openrag.core.logging import configure_logging
from openrag.modules.chat.llm import LLMStreamer
from openrag.modules.chat.service import Retriever
from openrag.modules.models.sync import sync_models_to_litellm
from openrag.modules.retrieval.service import retrieve


def create_app(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    redis_client: Redis | None = None,
    litellm_transport: httpx.AsyncBaseTransport | None = None,
    retriever: Retriever | None = None,
    llm_streamer: LLMStreamer | None = None,
) -> FastAPI:
    configure_logging()
    settings = get_settings()
    owned_engine: AsyncEngine | None = None
    if session_factory is None:
        owned_engine = build_engine(settings.database_url)
        session_factory = build_session_factory(owned_engine)
    owns_redis = redis_client is None
    if redis_client is None:
        redis_client = Redis.from_url(settings.redis_url)
    logger = structlog.get_logger("openrag.api")

    @asynccontextmanager
    async def lifespan(runtime_app: FastAPI) -> AsyncIterator[None]:
        try:
            async with runtime_app.state.session_factory() as session:
                deployed = await sync_models_to_litellm(
                    session,
                    settings,
                    transport=runtime_app.state.litellm_transport,
                )
            logger.info("litellm_startup_sync", deployed=deployed)
        except Exception as exc:  # noqa: BLE001
            logger.warning("litellm_startup_sync_failed", error=str(exc))

        try:
            yield
        finally:
            if owns_redis:
                await runtime_app.state.redis.aclose()
            if owned_engine is not None:
                await owned_engine.dispose()

    app = FastAPI(
        title="OpenRAG",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        UploadBodyLimitMiddleware,
        maximum_bytes=(
            settings.max_upload_mb * 1024 * 1024
            + settings.upload_multipart_overhead_kb * 1024
        ),
    )
    app.state.session_factory = session_factory
    app.state.redis = redis_client
    app.state.litellm_transport = litellm_transport
    app.state.retriever = retriever if retriever is not None else retrieve
    app.state.llm_streamer = llm_streamer

    def problem(status: int, title: str, detail: str) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={
                "type": "about:blank",
                "title": title,
                "status": status,
                "detail": detail,
            },
            media_type="application/problem+json",
        )

    @app.exception_handler(OpenRAGError)
    async def handle_openrag_error(
        request: Request,
        exc: OpenRAGError,
    ) -> JSONResponse:
        return problem(exc.status_code, exc.title, exc.detail)

    @app.exception_handler(IntegrityError)
    async def handle_integrity_error(
        request: Request,
        exc: IntegrityError,
    ) -> JSONResponse:
        logger.warning(
            "integrity_error",
            method=request.method,
            path=request.url.path,
        )
        return problem(409, "Conflict", "resource conflicts with existing state")

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unhandled_exception",
            method=request.method,
            path=request.url.path,
            client=request.client.host if request.client else "unknown",
            exc_info=exc,
        )
        return problem(500, "Internal error", "an unexpected error occurred")

    app.include_router(admin_secrets_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(chats_router, prefix="/api/v1")
    app.include_router(documents_router, prefix="/api/v1")
    app.include_router(embedding_profiles_router, prefix="/api/v1")
    app.include_router(health_router)
    app.include_router(models_router, prefix="/api/v1")
    app.include_router(roles_router, prefix="/api/v1")
    app.include_router(search_router, prefix="/api/v1")
    app.include_router(users_router, prefix="/api/v1")
    app.include_router(workspaces_router, prefix="/api/v1")
    return app
