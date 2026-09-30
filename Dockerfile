# syntax=docker/dockerfile:1

# LogScope API — non-root, single-process container
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS base

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest@sha256:a7aed3216253ee804de3e2d8afa5073baa1a177335345d43845cd4165e43b711 /uv /usr/local/bin/uv

# Create non-root user
RUN useradd --system --no-create-home --shell /usr/sbin/nologin logscope

WORKDIR /app

# Install dependencies first for layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and install project
COPY src/ src/
COPY examples/ examples/
RUN uv sync --frozen --no-dev

USER logscope

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --retries=3 --start-period=5s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live')"]

# One Uvicorn process per container
CMD ["/app/.venv/bin/uvicorn", "logscope.api.app:create_app", \
     "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--timeout-graceful-shutdown", "135", \
     "--no-access-log"]
