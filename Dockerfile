# syntax=docker/dockerfile:1

# ---- build: resolve the locked runtime dependencies into a virtualenv -------------------
FROM ghcr.io/astral-sh/uv:0.9.1 AS uv
FROM python:3.12-slim AS build

COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
# Dependencies first so this layer is cached until uv.lock changes.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --no-install-project

# Then the project itself, installed as a regular (non-editable) package.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --no-editable

# ---- runtime: just Python, the virtualenv, configs and schemas --------------------------
FROM python:3.12-slim

RUN groupadd --system --gid 10001 elt \
 && useradd --system --uid 10001 --gid elt --home-dir /app --no-create-home elt

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY config ./config
COPY schemas ./schemas

ENV PATH=/app/.venv/bin:$PATH \
    ELT_HOME=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER 10001
ENTRYPOINT ["elt"]
CMD ["list"]
