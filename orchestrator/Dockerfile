FROM python:3.11-slim

COPY --from=docker:29-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/usr/local \
    UV_NO_DEV=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY src/ src/
RUN uv sync --frozen --no-editable

COPY scripts/ scripts/

COPY dagster.yaml /opt/dagster/dagster.yaml
ENV DAGSTER_HOME=/opt/dagster

EXPOSE 4000

HEALTHCHECK --timeout=1s --start-period=3s --interval=3s --retries=20 \
    CMD ["dagster", "api", "grpc-health-check", "-p", "4000"]

CMD ["dagster", "code-server", "start", "-h", "0.0.0.0", "-p", "4000", "-m", "orchestrator.definitions"]
