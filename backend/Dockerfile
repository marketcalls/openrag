FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN groupadd --system openrag \
    && useradd --system --gid openrag --create-home openrag \
    && mkdir -p /data \
    && chown openrag:openrag /data

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src

RUN uv sync --frozen --no-dev

USER openrag

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "--factory", "openrag.api.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
