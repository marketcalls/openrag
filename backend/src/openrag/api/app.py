from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openrag.api.routes.auth import router as auth_router
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

    @app.exception_handler(OpenRAGError)
    async def handle_openrag_error(
        request: Request,
        exc: OpenRAGError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "type": "about:blank",
                "title": exc.title,
                "status": exc.status_code,
                "detail": exc.detail,
            },
            media_type="application/problem+json",
        )

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(workspaces_router, prefix="/api/v1")
    return app
