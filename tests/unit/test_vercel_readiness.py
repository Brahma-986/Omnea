"""Unit tests verifying Omnea deployment readiness for Vercel Hobby Free Tier."""

import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from procure_core.config import Settings
from procure_core.security.malware_scanner import QuarantineManager
from procure_core.security.secrets import EncryptedFileSecretsManager


def _get_vercel_app() -> Any:
    """Load the ASGI application instance exactly as Vercel Python runtime does."""
    spec = importlib.util.spec_from_file_location("vercel_index", Path("api/index.py").resolve())
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.app


def test_api_index_vercel_entrypoint() -> None:
    """Verify api/index.py loads the FastAPI application without import errors."""
    app = _get_vercel_app()
    assert app is not None
    assert app.title == "omnea-procure-os"


def test_vercel_json_configuration() -> None:
    """Verify vercel.json contains valid build command, output directory, and routing rewrites."""
    vercel_path = Path("vercel.json")
    assert vercel_path.exists(), "vercel.json must exist at project root"

    cfg = json.loads(vercel_path.read_text(encoding="utf-8"))

    # Support either top-level (standard) or services-level (multi-service) configuration
    if "services" in cfg:
        services = cfg["services"]
        assert "app" in services or "web" in services
        app_svc = services.get("app") or services.get("web", {})
        assert app_svc.get("root") == "public" or app_svc.get("outputDirectory") == "public", (
            "Frontend app service must target public/ directory for Edge CDN static serving"
        )
        assert services.get("api", {}).get("framework") == "fastapi"

        rewrites = cfg.get("rewrites", [])
        assert len(rewrites) >= 2

        api_rewrite = next(
            (
                r
                for r in rewrites
                if r.get("destination") == {"service": "api"}
                or "api/index.py" in str(r.get("destination", ""))
            ),
            None,
        )
        assert api_rewrite is not None, "Must have rewrite pointing to api service"
    else:
        assert cfg.get("buildCommand") == "npm run build"
        assert cfg.get("outputDirectory") == "public"

        rewrites = cfg.get("rewrites", [])
        assert len(rewrites) >= 2

        # Verify API rewrites route to api/index.py
        api_rewrite = next(
            (r for r in rewrites if "api/index.py" in str(r.get("destination", ""))),
            None,
        )
        assert api_rewrite is not None, "Must have rewrite pointing to api/index.py"

        # Verify SPA rewrites route to /index.html
        spa_rewrite = next(
            (r for r in rewrites if "/index.html" in str(r.get("destination", ""))),
            None,
        )
        assert spa_rewrite is not None, "Must have rewrite pointing to /index.html"


def test_public_static_assets_built() -> None:
    """Verify frontend build assets exist in public/ directory for Vercel Edge CDN."""
    public_dir = Path("public")
    assert public_dir.exists(), "public/ directory must exist"
    assert (public_dir / "index.html").exists(), "public/index.html must exist"
    assert (public_dir / "static" / "css" / "tokens.css").exists()
    assert (public_dir / "static" / "js" / "app.js").exists()
    assert (public_dir / "static" / "js" / "api.js").exists()


def test_database_url_normalization() -> None:
    """Verify DATABASE_URL correctly adapts for asyncpg and psycopg with SSL support."""
    # 1. Supabase pooler URL
    supabase_url = (
        "postgresql://postgres.ref:secret@aws-0-us-east-1.pooler.supabase.com:6543/postgres"
    )
    settings = Settings(DATABASE_URL=supabase_url)
    assert settings.async_database_url.startswith("postgresql+asyncpg://")
    assert settings.sync_database_url.startswith("postgresql+psycopg://")
    assert "6543" in settings.async_database_url

    # 2. Neon URL with sslmode=require
    neon_url = "postgres://alex:pass123@ep-cool-fog.us-east-2.aws.neon.tech/neondb?sslmode=require"
    settings_neon = Settings(DATABASE_URL=neon_url)
    assert settings_neon.async_database_url.startswith("postgresql+asyncpg://")
    assert "ssl=require" in settings_neon.async_database_url
    assert settings_neon.sync_database_url.startswith("postgresql+psycopg://")

    # 3. Vercel POSTGRES_URL alias
    settings_vercel = Settings(DATABASE_URL=None, POSTGRES_URL=supabase_url)
    assert settings_vercel.async_database_url.startswith("postgresql+asyncpg://")


def test_serverless_nullpool_detection(monkeypatch) -> None:
    """Verify NullPool is activated when VERCEL=1 or DB_SERVERLESS_POOL=True."""
    from sqlalchemy.pool import NullPool

    from procure_core.db import dispose_async_engine, get_async_engine

    # Simulate Vercel serverless environment
    monkeypatch.setenv("VERCEL", "1")
    settings = Settings(
        DATABASE_URL="postgresql://user:pass@localhost:5432/testdb",
        DB_SERVERLESS_POOL=True,
    )

    import asyncio

    asyncio.run(dispose_async_engine())
    engine = get_async_engine(settings)
    assert isinstance(engine.pool, NullPool)
    asyncio.run(dispose_async_engine())


def test_cors_vercel_wildcard(monkeypatch) -> None:
    """Verify CORS middleware allows *.vercel.app preview and production domains."""
    app = _get_vercel_app()
    client = TestClient(app)
    # Pre-flight OPTIONS request from a Vercel preview domain
    response = client.options(
        "/api/v1/status",
        headers={
            "Origin": "https://omnea-preview-abc123.vercel.app",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert (
        response.headers.get("access-control-allow-origin")
        == "https://omnea-preview-abc123.vercel.app"
    )


def test_read_only_filesystem_temp_fallback(monkeypatch) -> None:
    """Verify EncryptedFileSecretsManager and QuarantineManager fallback to /tmp in serverless."""
    monkeypatch.setenv("VERCEL", "1")

    secrets_mgr = EncryptedFileSecretsManager()
    assert str(tempfile.gettempdir()) in str(secrets_mgr.storage_dir)

    quarantine_mgr = QuarantineManager()
    assert str(tempfile.gettempdir()) in str(quarantine_mgr.base_dir)


def test_api_endpoints_via_testclient() -> None:
    """Verify critical API endpoints respond correctly on api.index.app."""
    app = _get_vercel_app()
    client = TestClient(app)

    # 1. Root / app route
    root_resp = client.get("/")
    assert root_resp.status_code == 200
    assert "text/html" in root_resp.headers["content-type"]

    # 2. Public status page (renders 200 if all live backends respond, 503 if backing DB/S3 degraded)
    status_resp = client.get("/status")
    assert status_resp.status_code in (200, 503)
    assert "Omnea System Status" in status_resp.text

    # 3. JSON status telemetry
    telemetry_resp = client.get("/api/v1/status")
    assert telemetry_resp.status_code in (200, 503)
    data = telemetry_resp.json()
    assert "services" in data
    assert "spend_budget" in data
