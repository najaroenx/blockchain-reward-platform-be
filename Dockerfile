FROM ghcr.io/astral-sh/uv:0.13.7 AS uv
FROM python:3.12-slim AS build

WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12-slim

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
COPY --from=build /app/.venv ./.venv
COPY alembic.ini ./
COPY migrations ./migrations

USER 10001:10001
CMD ["reward-api"]
