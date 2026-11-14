# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY apps/api ./apps/api
COPY pipelines ./pipelines
COPY contracts ./contracts
RUN uv sync --frozen --no-dev

FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    ANALOG_DATABASE_URL="sqlite+pysqlite:////data/analog.db" \
    ANALOG_MANIFEST_DIR="/app/pipelines/earth/manifests" \
    ANALOG_MANIFEST_DIR_CONTEXT="/app/pipelines/worldcover/manifests" \
    ANALOG_MANIFEST_DIR_PLANETARY="/app/pipelines/planetary/manifests" \
    ANALOG_AOI_RUNTIME_DIR="/data/aoi-catalog"

RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 libgomp1 libsqlite3-0 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 analog \
    && useradd --uid 10001 --gid analog --create-home analog \
    && mkdir -p /data \
    && chown analog:analog /data

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/apps /app/apps
COPY --from=builder /app/pipelines /app/pipelines
COPY --from=builder /app/contracts /app/contracts

USER analog
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)"

CMD ["uvicorn", "analog_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
