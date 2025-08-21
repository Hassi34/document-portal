# syntax=docker/dockerfile:1

# Multi-stage build using Astral's uv image for fast, reproducible installs

FROM ghcr.io/astral-sh/uv:python3.10-bookworm AS builder
WORKDIR /app

# Environment for reliable, quiet Python behavior
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    DOCKER_BUILDKIT=1

# Copy project manifests first for better layer caching (with lockfile)
COPY pyproject.toml uv.lock ./

# Create a local virtualenv with pinned runtime deps from the lockfile
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Copy application source and config (exclude data/logs via .dockerignore)
COPY src ./src
COPY configs ./configs
COPY README.md ./README.md


FROM python:3.10-slim-bookworm AS runtime
WORKDIR /app

# Use the venv created by uv in the builder stage
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"

# Minimal runtime libs for CPU FAISS and common wheels; keep it tiny
RUN apt-get update \
     && apt-get install -y --no-install-recommends \
         libstdc++6 \
         libgomp1 \
         ca-certificates \
     && rm -rf /var/lib/apt/lists/*

# Copy the virtual environment and app code
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/configs /app/configs
COPY --from=builder /app/README.md /app/README.md

# Create non-root user and ensure ownership of the app directory
RUN adduser --disabled-password --gecos '' appuser \
    && chown -R appuser:appuser /app

USER appuser

# App port (can be overridden)
EXPOSE 8080

# Start FastAPI with uvicorn
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
