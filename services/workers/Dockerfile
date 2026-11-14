FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY packages/procure_core/pyproject.toml packages/procure_core/
COPY packages/procure_context/pyproject.toml packages/procure_context/
COPY packages/procure_agents/pyproject.toml packages/procure_agents/
COPY services/api/pyproject.toml services/api/
COPY services/workers/pyproject.toml services/workers/

COPY packages/ packages/
COPY services/ services/

RUN uv sync --all-packages --no-dev

CMD ["uv", "run", "python", "-m", "workers.main"]
