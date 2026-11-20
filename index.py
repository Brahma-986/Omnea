"""Vercel Serverless Function entry point for Omnea Procure OS FastAPI Application.

This file provides the primary entrypoint for Vercel's Python runtime.
It imports the ASGI app instance from api.index.
"""

from __future__ import annotations

from api.index import app

__all__ = ["app"]
