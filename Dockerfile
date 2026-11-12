# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS deps
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

FROM deps AS build
COPY src ./src
COPY README.md ./
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN useradd --create-home --uid 1000 reward
COPY --from=build /app/.venv ./.venv
COPY --from=build /app/src ./src
COPY pyproject.toml alembic.ini ./
COPY migrations ./migrations
USER reward

FROM runtime AS api
EXPOSE 3000
CMD ["reward-api"]

FROM runtime AS worker
CMD ["reward-worker", "--check"]

FROM runtime AS indexer
CMD ["reward-indexer", "--check"]
