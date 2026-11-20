"""Vercel serverless entry point for internal workers service."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI

# Add repository root and package source directories to sys.path
workers_root = Path(__file__).resolve().parent.parent
repo_root = workers_root.parent.parent

for pkg in [
    "procure_core",
    "procure_ingress",
    "procure_context",
    "procure_agents",
]:
    pkg_src = repo_root / "packages" / pkg / "src"
    if pkg_src.exists() and str(pkg_src) not in sys.path:
        sys.path.insert(0, str(pkg_src))

workers_src = workers_root / "src"
if workers_src.exists() and str(workers_src) not in sys.path:
    sys.path.insert(0, str(workers_src))

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
