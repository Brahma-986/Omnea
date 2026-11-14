FROM python:3.11-slim-bookworm

# Install system dependencies (curl for healthchecks, libpq for postgres)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Astral uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /app

# Copy dependency specifications first to leverage caching
COPY pyproject.toml README.md ./
COPY packages/procure_core/pyproject.toml packages/procure_core/
COPY packages/procure_ingress/pyproject.toml packages/procure_ingress/
COPY packages/procure_context/pyproject.toml packages/procure_context/
COPY packages/procure_agents/pyproject.toml packages/procure_agents/
COPY services/api/pyproject.toml services/api/
COPY services/workers/pyproject.toml services/workers/

# Copy source code
COPY packages/ packages/
COPY services/ services/

# Install entire workspace
RUN uv sync --all-packages --no-dev

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
