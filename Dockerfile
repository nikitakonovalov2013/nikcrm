# syntax=docker/dockerfile:1.6

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install python deps in a cache-friendly layer.
# Only invalidates when pyproject/shared change.
COPY pyproject.toml ./
COPY shared ./shared

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -U pip \
    && pip install .

# Alembic metadata used by web entrypoint and migrator
COPY alembic.ini ./
COPY migrations ./migrations


FROM base AS web

COPY web ./web
COPY bot ./bot
COPY web/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]


FROM base AS bot

COPY bot ./bot
COPY web ./web
COPY bot/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]


FROM base AS migrator

CMD ["alembic", "upgrade", "head"]
