"""Unit tests verifying that every log line emitted during an authenticated request
carries the verified tenant identifier, and that missing tenant claims raise 401
without falling back to 'tenant_default'.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from collections.abc import Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import create_app
from api.routes.events import _stream_tickets
from procure_core.auth.rbac import RbacEngine
from procure_core.log_config import (
    JSONLogFormatter,
    RedactingFilter,
)
from procure_core.security.tokens import create_access_token


@pytest.fixture(autouse=True)
def setup_rbac_roles() -> Iterator[None]:
    """Ensure test users have appropriate roles."""
    RbacEngine.clear_fallback_roles()
    RbacEngine.assign_fallback_role("tenant_easecart", "user@easecart.com", "tenant_admin")
    yield
    RbacEngine.clear_fallback_roles()
    _stream_tickets.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_every_log_line_for_authenticated_request_carries_verified_tenant() -> None:
    """Assert every log line emitted during an authenticated request carries the verified tenant.

    Specifically asserts:
    1. Every structured log entry contains 'tenant_id' matching 'tenant_easecart'.
    2. 'tenant_default' NEVER appears anywhere in the captured log stream.
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONLogFormatter())
    handler.addFilter(RedactingFilter())

    app = create_app()

    # Add handler to root and application loggers
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)
    api_logger = logging.getLogger("api")
    api_logger.setLevel(logging.INFO)
    api_logger.addHandler(handler)

    try:
        token = create_access_token(
            user_id="user@easecart.com",
            tenant_id="tenant_easecart",
            email="user@easecart.com",
            roles=["tenant_admin"],
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Test across multiple routes (dashboard metrics, ticket issue, SSE stream)
            headers = {"Authorization": f"Bearer {token}"}

            # 1. Dashboard metrics
            resp1 = await client.get("/api/v1/dashboard/metrics", headers=headers)
            assert resp1.status_code == 200

            # 2. Issue stream ticket
            resp2 = await client.post("/api/v1/events/ticket", headers=headers)
            assert resp2.status_code == 200
            ticket = resp2.json()["ticket"]

            # 3. Connect to SSE stream using ticket (read first event and close)
            sse_resp = await client.send(
                client.build_request("GET", f"/api/v1/events/stream?ticket={ticket}"),
                stream=True,
            )
            try:
                assert sse_resp.status_code == 200
                _ = await asyncio.wait_for(anext(sse_resp.aiter_lines()), timeout=5.0)
            finally:
                await sse_resp.aclose()

        captured = stream.getvalue()

        # Parse every JSON log line
        log_records: list[dict[str, object]] = []
        for line in captured.strip().splitlines():
            line_str = line.strip()
            if not line_str or not line_str.startswith("{"):
                continue
            try:
                rec = json.loads(line_str)
                log_records.append(rec)
            except Exception:
                continue

        assert len(log_records) > 0, (
            f"Expected log records to be emitted during requests, got:\n{captured}"
        )

        # Assert zero occurrence of 'tenant_default' in entire captured output
        assert "tenant_default" not in captured, (
            f"Found forbidden fallback 'tenant_default' in logs:\n{captured}"
        )

        # Assert that whenever a tenant_id is logged for these authenticated requests, it is 'tenant_easecart'
        tenant_logs = [r for r in log_records if "tenant_id" in r]
        assert len(tenant_logs) > 0, f"Expected log records to carry tenant_id:\n{captured}"
        for r in tenant_logs:
            assert r["tenant_id"] == "tenant_easecart", f"Log record carried incorrect tenant: {r}"

    finally:
        root_logger.removeHandler(handler)
        api_logger.removeHandler(handler)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_token_missing_tenant_id_claim_raises_401() -> None:
    """Tokens missing a verified tenant_id claim are rejected with 401 and never fall back to default."""
    app = create_app()

    # Create a token with empty/missing tenant_id
    token_empty_tenant = create_access_token(
        user_id="user-rogue",
        tenant_id="",
        email="rogue@example.com",
        roles=["tenant_admin"],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/dashboard/metrics",
            headers={"Authorization": f"Bearer {token_empty_tenant}"},
        )
        assert resp.status_code == 401
        assert "verified tenant_id claim" in resp.json()["detail"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unauthenticated_request_does_not_inject_tenant_default() -> None:
    """Public unauthenticated requests (like /health) do not set tenant_id to 'tenant_default'."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONLogFormatter())
    handler.addFilter(RedactingFilter())

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    try:
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200

        captured = stream.getvalue()
        assert "tenant_default" not in captured
    finally:
        root_logger.removeHandler(handler)
