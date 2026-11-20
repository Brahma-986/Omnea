"""Vercel Serverless Function entry point for Omnea Procure OS FastAPI Application.

This file is automatically detected by Vercel's Python Serverless Runtime (@vercel/python).
It configures Python search paths to include all monorepo workspace packages and services,
and exposes the ASGI `app` instance.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Compute repository root directory dynamically at runtime
ROOT_DIR = Path(__file__).resolve().parent.parent

# Add workspace packages and services to Python sys.path
WORKSPACE_DIRS = [
    ROOT_DIR / "packages" / "procure_core" / "src",
    ROOT_DIR / "packages" / "procure_ingress" / "src",
    ROOT_DIR / "packages" / "procure_context" / "src",
    ROOT_DIR / "packages" / "procure_agents" / "src",
    ROOT_DIR / "services" / "api" / "src",
    ROOT_DIR / "services" / "workers" / "src",
]

for d in WORKSPACE_DIRS:
    d_str = str(d)
    if d_str not in sys.path:
        sys.path.insert(0, d_str)

# Import the FastAPI ASGI application instance
from api.main import app  # noqa: E402

# Export app for Vercel's ASGI serverless handler
__all__ = ["app"]
