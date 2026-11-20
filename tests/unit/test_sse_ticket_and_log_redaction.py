"""Unit tests for SSE single-use stream tickets, raw JWT query parameter rejection,
and log redaction of sensitive credentials.
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
    redact_sensitive_data,
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
def test_redact_sensitive_data_scrubs_jwts_and_secrets() -> None:
    """redact_sensitive_data removes all JWT patterns, query param credentials, and API keys."""
    raw_jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTYiLCJ0ZW5hbnRfaWQiOiJ0ZW5hbnRfZWFzZWNhcnQifQ."
        "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    )

    # 1. Standalone JWT
    scrubbed = redact_sensitive_data(f"Token is {raw_jwt}")
    assert "eyJ" not in scrubbed
    assert "[REDACTED]" in scrubbed

    # 2. URL Query parameter with token
    url_log = f"GET /api/v1/events/stream?token={raw_jwt}&other=123 HTTP/1.1"
    scrubbed_url = redact_sensitive_data(url_log)
    assert raw_jwt not in scrubbed_url
    assert "token=[REDACTED]" in scrubbed_url
    assert "other=123" in scrubbed_url

    # 3. URL Query parameter with ticket
    ticket_url = "GET /api/v1/events/stream?ticket=sec_abc1234567890 HTTP/1.1"
    assert "ticket=[REDACTED]" in redact_sensitive_data(ticket_url)

    # 4. Bearer header log
    auth_header_log = f"Authorization: Bearer {raw_jwt}"
    scrubbed_auth = redact_sensitive_data(auth_header_log)
    assert raw_jwt not in scrubbed_auth
    assert "Bearer [REDACTED]" in scrubbed_auth

    # 5. Omnea API Key
    key_log = "Using key omnea_live_abcdef1234567890_secret"
    scrubbed_key = redact_sensitive_data(key_log)
    assert "omnea_live" not in scrubbed_key
    assert "[REDACTED]" in scrubbed_key


@pytest.mark.unit
def test_no_jwt_appears_in_captured_json_logs() -> None:
    """Assert that when logging messages containing JWTs or tickets, the captured logs contain zero JWT or ticket strings."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONLogFormatter())
    handler.addFilter(RedactingFilter())

    test_logger = logging.getLogger("test.redaction.logger")
    test_logger.setLevel(logging.INFO)
    test_logger.addHandler(handler)
    test_logger.addFilter(RedactingFilter())

    raw_jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjY2YWVhZC0zZDFmLTQzYmYtOWE3NC0yNDY1YzA3M2Q1YjAiLCJ0ZW5hbnRfaWQiOiJ0ZW5hbnRfZWFzZWNhcnQifQ."
        "Jws5DWhjuw2xnGCPpWBoYWUJ0Fb9UK5vrFygNVuUmc8"
    )
    raw_ticket = "sec_stream_ticket_998877665544332211"

    test_logger.info(
        '127.0.0.1:55976 - "GET /api/v1/events/stream?token=%s HTTP/1.1" 200 OK',
        raw_jwt,
    )
    test_logger.info(
        '127.0.0.1:55976 - "GET /api/v1/events/stream?ticket=%s HTTP/1.1" 200 OK',
        raw_ticket,
    )
    test_logger.info("Emitted event with Bearer token: Bearer %s", raw_jwt)
    test_logger.info("Extra log payload", extra={"auth_token": raw_jwt, "client_id": "c-101"})

    captured_output = stream.getvalue()

    # Core assertion: ZERO occurrence of 'eyJ' or raw ticket in captured logs
    assert "eyJ" not in captured_output, (
        f"Raw JWT was not redacted from log output:\n{captured_output}"
    )
    assert raw_ticket not in captured_output, (
        f"Raw ticket was not redacted from log output:\n{captured_output}"
    )
    assert "[REDACTED]" in captured_output

    # Validate that logs remain valid JSON Lines
    lines = [line.strip() for line in captured_output.strip().splitlines() if line.strip()]
    assert len(lines) == 4
    for line in lines:
        parsed = json.loads(line)
        assert "timestamp" in parsed
        assert "message" in parsed


@pytest.mark.unit
@pytest.mark.asyncio
async def test_post_stream_ticket_requires_auth() -> None:
    """POST /api/v1/events/ticket requires authenticated caller."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/events/ticket")
        assert resp.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_post_stream_ticket_issues_valid_ticket() -> None:
    """POST /api/v1/events/ticket generates short-lived single-use ticket expiring in ~30s."""
    app = create_app()
    token = create_access_token(
        user_id="user-1",
        tenant_id="tenant_easecart",
        email="user@easecart.com",
        roles=["tenant_admin"],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/events/ticket",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "ticket" in data
        assert data["expires_in"] == 30
        ticket = data["ticket"]
        assert len(ticket) >= 32
        assert ticket in _stream_tickets
        # Verify ticket is tenant-bound
        user_id, ticket_tenant, _roles, _exp = _stream_tickets[ticket]
        assert user_id == "user-1"
        assert ticket_tenant == "tenant_easecart"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sse_stream_single_use_consumption() -> None:
    """Stream ticket is consumed on first connection, tenant-bound, and cannot be replayed."""
    app = create_app()
    token = create_access_token(
        user_id="user-1",
        tenant_id="tenant_easecart",
        email="user@easecart.com",
        roles=["tenant_admin"],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Issue ticket
        ticket_res = await client.post(
            "/api/v1/events/ticket",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert ticket_res.status_code == 200
        ticket = ticket_res.json()["ticket"]

        # 2. First connection with ticket succeeds (read initial event and close immediately)
        stream_resp = await client.send(
            client.build_request("GET", f"/api/v1/events/stream?ticket={ticket}"),
            stream=True,
        )
        try:
            assert stream_resp.status_code == 200
            assert "text/event-stream" in stream_resp.headers["content-type"]
            first_line = await asyncio.wait_for(anext(stream_resp.aiter_lines()), timeout=5.0)
            assert "event: connected" in first_line
        finally:
            await stream_resp.aclose()

        # 3. Ticket is now consumed (deleted from memory)
        assert ticket not in _stream_tickets

        # 4. Replay attempt with same ticket is rejected with 401
        replay_resp = await client.get(f"/api/v1/events/stream?ticket={ticket}")
        assert replay_resp.status_code == 401
        assert "Invalid, expired, or already used" in replay_resp.json()["detail"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sse_stream_raw_jwt_in_query_rejected() -> None:
    """Passing raw JWT in ?token= query parameter is strictly forbidden and returns 400 Bad Request."""
    app = create_app()
    raw_jwt = create_access_token(
        user_id="user-1",
        tenant_id="tenant_easecart",
        email="user@easecart.com",
        roles=["tenant_admin"],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/v1/events/stream?token={raw_jwt}")
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "forbidden for security" in detail


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sse_stream_http_only_cookie_auth() -> None:
    """SSE stream accepts authentication via access_token httpOnly cookie."""
    app = create_app()
    token = create_access_token(
        user_id="user-1",
        tenant_id="tenant_easecart",
        email="user@easecart.com",
        roles=["tenant_admin"],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Set cookie directly on client
        client.cookies.set("access_token", token)
        stream_resp = await client.send(
            client.build_request("GET", "/api/v1/events/stream"),
            stream=True,
        )
        try:
            assert stream_resp.status_code == 200
            assert "text/event-stream" in stream_resp.headers["content-type"]
            first_line = await asyncio.wait_for(anext(stream_resp.aiter_lines()), timeout=5.0)
            assert "event: connected" in first_line
        finally:
            await stream_resp.aclose()
