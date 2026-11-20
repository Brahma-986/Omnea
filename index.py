"""Vercel Serverless Function entry point for Omnea Procure OS FastAPI Application.

This file provides the primary entrypoint for Vercel's Python runtime (@vercel/python).
It exposes the ASGI `app` instance from the installed `api` package.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Fallback: if running in local development where packages are editable or loose,
# ensure repository workspace directories are reachable
ROOT_DIR = Path(__file__).resolve().parent
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
    # Append at the end (not insert at 0) so site-packages has priority in production builds
    if d.exists() and d_str not in sys.path:
        sys.path.append(d_str)

from api.main import app  # noqa: E402

__all__ = ["app"]
