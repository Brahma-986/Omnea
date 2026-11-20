"""Unit tests for SSE single-use stream tickets, raw JWT query parameter rejection,
log redaction of sensitive credentials, and cross-worker persistent ticket store.
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
from procure_core.auth.tickets import (
    PostgresStreamTicketStore,
    StreamTicketStore,
    get_stream_ticket_store,
    set_stream_ticket_store_override,
)
from procure_core.log_config import (
    JSONLogFormatter,
    RedactingFilter,
    redact_sensitive_data,
)
from procure_core.security.tokens import create_access_token
from tests.fakes.fake_stream_ticket_store import FakeStreamTicketStore


@pytest.fixture(autouse=True)
def setup_rbac_roles() -> Iterator[None]:
    """Ensure test users have appropriate roles and reset ticket stores."""
    RbacEngine.clear_fallback_roles()
    RbacEngine.assign_fallback_role("tenant_easecart", "user@easecart.com", "tenant_admin")
    fake_store = FakeStreamTicketStore()
    set_stream_ticket_store_override(fake_store)
    _stream_tickets.clear()
    yield
    RbacEngine.clear_fallback_roles()
    set_stream_ticket_store_override(None)
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
def test_redaction_extended_to_auth_headers_refresh_tokens_cookies_and_api_keys() -> None:
    """Assert redaction handles Authorization headers (Bearer/Basic/raw), refresh tokens, cookies, and non-JWT keys."""
    # 1. Non-Bearer raw Authorization header
    raw_auth = "Authorization: raw_secret_token_value_998877"
    redacted_auth = redact_sensitive_data(raw_auth)
    assert "raw_secret_token_value_998877" not in redacted_auth
    assert "Authorization: [REDACTED]" in redacted_auth

    # 2. Basic Authorization header
    basic_auth = "Authorization: Basic dXNlcjpzdXBlcnNlY3JldA=="
    redacted_basic = redact_sensitive_data(basic_auth)
    assert "dXNlcjpzdXBlcnNlY3JldA==" not in redacted_basic
    assert "Basic [REDACTED]" in redacted_basic

    # 3. Refresh token in URL query parameter
    url_refresh = "POST /api/v1/auth/refresh?refresh_token=sec_refresh_abc1234567890 HTTP/1.1"
    redacted_url_refresh = redact_sensitive_data(url_refresh)
    assert "sec_refresh_abc1234567890" not in redacted_url_refresh
    assert "refresh_token=[REDACTED]" in redacted_url_refresh

    # 4. Refresh token in key-value and JSON log payloads
    json_refresh = '{"action": "refresh", "refresh_token": "sec_refresh_token_999888"}'
    redacted_json_refresh = redact_sensitive_data(json_refresh)
    assert "sec_refresh_token_999888" not in redacted_json_refresh
    assert '"refresh_token": "[REDACTED]"' in redacted_json_refresh

    # 5. Cookies in Cookie header and Cookie string values
    cookie_log = "Cookie: access_token=eyJhbGciOi...; refresh_token=sec_ref_cookie_val_123; session_id=sess_456"
    redacted_cookie = redact_sensitive_data(cookie_log)
    assert "sec_ref_cookie_val_123" not in redacted_cookie
    assert "refresh_token=[REDACTED]" in redacted_cookie
    assert "access_token=[REDACTED]" in redacted_cookie

    # 6. Non-JWT API key passed via X-API-Key header
    raw_api_key = "secret_custom_api_key_xyz_777888999"
    x_api_key_log = f"Incoming request with X-API-Key: {raw_api_key}"
    redacted_api_key = redact_sensitive_data(x_api_key_log)
    assert raw_api_key not in redacted_api_key
    assert "X-API-Key: [REDACTED]" in redacted_api_key

    # 7. Non-JWT API key passed via query parameter
    query_key_log = "GET /api/v1/resource?api_key=custom_non_jwt_secret_key_888 HTTP/1.1"
    redacted_query_key = redact_sensitive_data(query_key_log)
    assert "custom_non_jwt_secret_key_888" not in redacted_query_key
    assert "api_key=[REDACTED]" in redacted_query_key

    # 8. sk_live non-JWT secret key
    sk_key_log = "Stripe or service key sk_live_1234567890abcdef123456 configured"
    redacted_sk = redact_sensitive_data(sk_key_log)
    assert "sk_live_1234567890abcdef123456" not in redacted_sk
    assert "[REDACTED]" in redacted_sk


@pytest.mark.unit
def test_no_jwt_or_secret_appears_in_captured_json_logs() -> None:
    """Assert that when logging messages containing JWTs, tickets, cookies, or secrets, the captured logs contain zero secrets."""
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
    raw_refresh = "sec_refresh_val_0011223344"
    raw_non_jwt_key = "non_jwt_custom_api_key_445566"

    test_logger.info(
        '127.0.0.1:55976 - "GET /api/v1/events/stream?token=%s HTTP/1.1" 200 OK',
        raw_jwt,
    )
    test_logger.info(
        '127.0.0.1:55976 - "GET /api/v1/events/stream?ticket=%s HTTP/1.1" 200 OK',
        raw_ticket,
    )
    test_logger.info("Emitted event with Bearer token: Bearer %s", raw_jwt)
    test_logger.info("Refresh attempt with refresh_token: %s", raw_refresh)
    test_logger.info("Header X-API-Key: %s", raw_non_jwt_key)
    test_logger.info(
        "Cookie auth: Cookie: access_token=%s; refresh_token=%s",
        raw_jwt,
        raw_refresh,
    )
    test_logger.info("Extra log payload", extra={"auth_token": raw_jwt, "client_id": "c-101"})

    captured_output = stream.getvalue()

    # Core assertion: ZERO occurrence of raw credentials
    assert "eyJ" not in captured_output
    assert raw_ticket not in captured_output
    assert raw_refresh not in captured_output
    assert raw_non_jwt_key not in captured_output
    assert "[REDACTED]" in captured_output

    # Validate that logs remain valid JSON Lines
    lines = [line.strip() for line in captured_output.strip().splitlines() if line.strip()]
    assert len(lines) == 7
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
        store = get_stream_ticket_store()
        assert ticket in store
        # Verify ticket is tenant-bound
        user_id, ticket_tenant, _roles, _exp = store[ticket]
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

        # 3. Ticket is now consumed (deleted from store)
        store = get_stream_ticket_store()
        assert ticket not in store

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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stream_ticket_store_interface_and_lifecycle() -> None:
    """Test StreamTicketStore interface contract with FakeStreamTicketStore."""
    store: StreamTicketStore = FakeStreamTicketStore()

    # 1. Create ticket
    ticket = await store.create_ticket(
        user_id="usr-abc",
        tenant_id="tenant_acme",
        roles=["admin", "finance"],
        ttl_seconds=0.1,  # short ttl for expiry test
    )
    assert len(ticket) >= 32

    # 2. Consume ticket successfully
    consumed = await store.consume_ticket(ticket)
    assert consumed is not None
    user_id, tenant_id, roles = consumed
    assert user_id == "usr-abc"
    assert tenant_id == "tenant_acme"
    assert "admin" in roles

    # 3. Single-use: consuming again returns None
    assert await store.consume_ticket(ticket) is None

    # 4. Expiration
    ticket2 = await store.create_ticket(
        user_id="usr-xyz",
        tenant_id="tenant_acme",
        roles=["viewer"],
        ttl_seconds=0.01,
    )
    await asyncio.sleep(0.02)
    # Consuming expired ticket returns None
    assert await store.consume_ticket(ticket2) is None

    # 5. Cleanup expired
    cleaned = await store.cleanup_expired()
    assert cleaned >= 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgres_stream_ticket_store_atomic_consumption() -> None:
    """Verify PostgreSQL-backed ticket store handles persistence, restarts, and atomic single-use consumption."""
    store = PostgresStreamTicketStore()

    # 1. Create ticket in PostgreSQL
    ticket = await store.create_ticket(
        user_id="usr-pg-1",
        tenant_id="tenant_pg_acme",
        roles=["tenant_admin"],
        ttl_seconds=30.0,
    )
    assert isinstance(ticket, str) and len(ticket) >= 32

    # 2. Simulate worker restart / second worker reading from Postgres
    worker2_store = PostgresStreamTicketStore()
    consumed = await worker2_store.consume_ticket(ticket)
    assert consumed is not None
    u_id, t_id, r_list = consumed
    assert u_id == "usr-pg-1"
    assert t_id == "tenant_pg_acme"
    assert r_list == ["tenant_admin"]

    # 3. Replay / duplicate consumption attempt fails atomically
    replay = await store.consume_ticket(ticket)
    assert replay is None

    # 4. Expired ticket cleanup
    exp_ticket = await store.create_ticket(
        user_id="usr-exp",
        tenant_id="tenant_pg_acme",
        roles=["user"],
        ttl_seconds=-1.0,  # Already expired
    )
    assert await store.consume_ticket(exp_ticket) is None
    cleaned = await store.cleanup_expired()
    assert cleaned >= 0
