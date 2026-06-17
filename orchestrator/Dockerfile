FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ src/
RUN uv sync --frozen --no-dev

COPY dagster.yaml /opt/dagster/dagster.yaml
ENV DAGSTER_HOME=/opt/dagster

EXPOSE 4000

HEALTHCHECK --timeout=1s --start-period=3s --interval=3s --retries=20 \
    CMD ["uv", "run", "dagster", "api", "grpc-health-check", "-p", "4000"]

CMD ["uv", "run", "dagster", "code-server", "start", "-h", "0.0.0.0", "-p", "4000", "-m", "orchestrator.definitions"]
