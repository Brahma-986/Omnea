"""Vercel serverless entry point for internal workers service.

This module exposes the FastAPI ASGI application for background task processing
and queue operations on Vercel Serverless.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Compute repository root directory dynamically at runtime
ROOT_DIR = Path(__file__).resolve().parent

# Add workspace packages and services to Python sys.path
WORKSPACE_DIRS = [
    ROOT_DIR / "packages" / "procure_core" / "src",
    ROOT_DIR / "packages" / "procure_ingress" / "src",
    ROOT_DIR / "packages" / "procure_context" / "src",
    ROOT_DIR / "packages" / "procure_agents" / "src",
    ROOT_DIR / "services" / "workers" / "src",
]

for d in WORKSPACE_DIRS:
    d_str = str(d)
    if d.exists() and d_str not in sys.path:
        sys.path.insert(0, d_str)

from fastapi import FastAPI  # noqa: E402

app = FastAPI(
    title="omnea-workers-service",
    description="Internal background task and queue processor service",
    version="0.1.0",
)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Internal health check for the workers service."""
    return {"status": "ok", "service": "workers"}


@app.post("/tasks/reconcile")
async def trigger_reconcile() -> dict[str, str]:
    """Trigger background reconciliation run."""
    return {"status": "accepted", "task": "reconcile"}
