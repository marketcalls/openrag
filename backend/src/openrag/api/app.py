import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.api.routes.auth import router as auth_router
from openrag.api.routes.health import router as health_router
from openrag.api.routes.users import router as users_router
from openrag.api.routes.workspaces import router as workspaces_router
from openrag.core.config import get_settings
from openrag.core.db import build_engine, build_session_factory
from openrag.core.errors import OpenRAGError
from openrag.core.logging import configure_logging


def create_app(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="OpenRAG",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    if session_factory is None:
        engine = build_engine(get_settings().database_url)
        session_factory = build_session_factory(engine)
    app.state.session_factory = session_factory
    logger = structlog.get_logger("openrag.api")

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

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(health_router)
    app.include_router(users_router, prefix="/api/v1")
    app.include_router(workspaces_router, prefix="/api/v1")
    return app
