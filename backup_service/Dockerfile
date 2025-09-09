FROM ghcr.io/astral-sh/uv:python3.10-bookworm AS build
WORKDIR /app

# Copy project metadata first for dependency layer caching
COPY pyproject.toml README.md ./
COPY backup_service ./backup_service
RUN uv pip install --system --no-cache -e .

FROM python:3.10-slim AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Copy only installed site-packages and source
COPY --from=build /usr/local/lib/python3.10 /usr/local/lib/python3.10
COPY --from=build /usr/local/bin /usr/local/bin
COPY backup_service ./backup_service
COPY backup_config.yaml ./backup_config.yaml

# Non-root user (optional hardening)
RUN useradd -u 1000 backup && chown -R backup:backup /app
RUN mkdir -p /app/data /app/logs && chown -R backup:backup /app/data /app/logs
USER backup

ENTRYPOINT ["python", "-m", "backup_service.cli"]
