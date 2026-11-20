# Multi-stage production container for Omnea Platform
FROM python:3.11-slim-bookworm AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install system build dependencies and security updates
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:0.5.24 /uv /uvx /bin/

# Copy workspace metadata files for layer caching
COPY pyproject.toml uv.lock ./
COPY packages/procure_core/pyproject.toml packages/procure_core/
COPY packages/procure_ingress/pyproject.toml packages/procure_ingress/
COPY packages/procure_context/pyproject.toml packages/procure_context/
COPY packages/procure_agents/pyproject.toml packages/procure_agents/
COPY services/api/pyproject.toml services/api/
COPY services/workers/pyproject.toml services/workers/

# Copy source tree
COPY packages/ packages/
COPY services/ services/
COPY infra/ infra/
COPY alembic.ini ./

# Sync dependencies and compile wheel packages
RUN uv sync --frozen --no-dev --all-packages

# -----------------------------------------------------------------------------
# Production Runtime Stage
# -----------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Install runtime shared libraries and curl for health probes
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq5 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 appgroup \
    && useradd -u 10001 -g appgroup -s /bin/bash -m appuser

# Copy virtualenv and project files from builder
COPY --from=builder --chown=appuser:appgroup /app /app

# Run as non-privileged unrooted user
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
