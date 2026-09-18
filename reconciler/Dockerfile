FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/usr/local \
    UV_NO_DEV=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY recon/ recon/
RUN uv sync --frozen --no-editable
