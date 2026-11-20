"""Unit and acceptance tests for Stage 2:
Real-time SSE, Notifications, Role Dashboards, and Security.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator

import pytest
from httpx import AsyncClient

from api.routes.events import broadcast_tenant_event
from procure_core.auth.rbac import RbacEngine


@pytest.fixture(autouse=True)
def setup_stage2_test_roles() -> Iterator[None]:
    """Configure RBAC roles for unit testing Stage 2 endpoints."""
    RbacEngine.clear_fallback_roles()

    # User / Requester
    RbacEngine.assign_fallback_role("tenant_test", "alice@omnea.ai", "requester")
    RbacEngine.assign_fallback_role("tenant_test", "requester@omnea.ai", "requester")

    # Approver
    RbacEngine.assign_fallback_role("tenant_test", "approver@omnea.ai", "approver")

    # Finance Admin
    RbacEngine.assign_fallback_role("tenant_test", "finance@omnea.ai", "finance_admin")

    # Security Reviewer
    RbacEngine.assign_fallback_role("tenant_test", "sec_lead@omnea.ai", "security_reviewer")

    # Legal Reviewer
    RbacEngine.assign_fallback_role("tenant_test", "counsel@omnea.ai", "legal_reviewer")

    # Tenant Admin
    RbacEngine.assign_fallback_role("tenant_test", "admin@omnea.ai", "tenant_admin")

    yield

    RbacEngine.clear_fallback_roles()


# =============================================================================
# 1. Security Headers & CSRF Middleware Tests
# =============================================================================


@pytest.mark.unit
async def test_security_headers_middleware(async_client: AsyncClient) -> None:
    """SecurityHeadersMiddleware adds strict CSP and anti-clickjacking headers to all responses."""
    resp = await async_client.get("/health")
    assert resp.status_code == 200

    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    script_policy = csp.split("script-src")[1].split(";")[0]
    assert "'unsafe-inline'" not in script_policy, "script-src must not allow unsafe-inline!"
    assert "frame-ancestors 'none'" in csp

    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"


@pytest.mark.unit
async def test_csrf_validation_middleware(async_client: AsyncClient) -> None:
    """Mutating methods require X-CSRF-Token except for exempt auth endpoints and webhooks."""
    token = "admin@omnea.ai:tenant_test"
    auth_headers = {"Authorization": f"Bearer {token}"}

    # 1. Mutating call without X-CSRF-Token -> 403 Forbidden
    resp_no_csrf = await async_client.post(
        "/api/v1/notifications/test-notif-1/read",
        headers=auth_headers,
    )
    assert resp_no_csrf.status_code == 403
    assert "CSRF token missing or invalid" in resp_no_csrf.json()["detail"]

    # 2. Mutating call with valid X-CSRF-Token -> 200 OK
    csrf_headers = {
        "Authorization": f"Bearer {token}",
        "X-CSRF-Token": "valid-stage2-csrf-token-123456",
    }
    resp_with_csrf = await async_client.post(
        "/api/v1/notifications/test-notif-1/read",
        headers=csrf_headers,
    )
    assert resp_with_csrf.status_code == 200
    assert resp_with_csrf.json()["is_read"] is True


@pytest.mark.unit
async def test_csrf_exempt_public_endpoints(async_client: AsyncClient) -> None:
    """Public auth endpoints and webhooks bypass CSRF validation without error."""
    # Auth login does not require CSRF token
    rnd_email = f"csrf_check_{uuid.uuid4().hex[:8]}@omnea.ai"
    resp_login = await async_client.post(
        "/auth/login",
        json={"email": rnd_email, "password": "DummyPassword123!"},
    )
    # Expected 401 Unauthorized (invalid credentials), NOT 403 CSRF error
    assert resp_login.status_code == 401
    assert "CSRF" not in resp_login.text


# =============================================================================
# 2. SPA & Static Asset Routing Tests
# =============================================================================


@pytest.mark.unit
async def test_spa_web_routes(async_client: AsyncClient) -> None:
    """GET / and GET /app and /app/{path} return static/index.html SPA shell."""
    for path in ["/", "/app", "/app/dashboard", "/app/finance/spend"]:
        resp = await async_client.get(path)
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert "Omnea" in resp.text

    # Docs and openapi remain accessible
    resp_docs = await async_client.get("/docs")
    assert resp_docs.status_code == 200

    resp_openapi = await async_client.get("/openapi.json")
    assert resp_openapi.status_code == 200


# =============================================================================
# 3. Real-Time SSE Stream & Broadcast Tests
# =============================================================================


@pytest.mark.unit
async def test_sse_broadcast_tenant_event_helper() -> None:
    """broadcast_tenant_event supports both sync and async awaitable invocation."""
    tenant = "tenant_test_sse"
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()

    from api.routes.events import _add_subscriber, _remove_subscriber

    _add_subscriber(tenant, queue)

    try:
        # 1. Sync broadcast
        delivered_sync = broadcast_tenant_event(tenant, "request_created", {"request_id": "r-101"})
        assert int(delivered_sync) == 1
        msg = queue.get_nowait()
        assert msg["event"] == "request_created"
        assert msg["data"]["request_id"] == "r-101"

        # 2. Async broadcast (awaitable)
        delivered_async = await broadcast_tenant_event(tenant, "approval_needed", {"step": 2})
        assert delivered_async == 1
        msg2 = queue.get_nowait()
        assert msg2["event"] == "approval_needed"
        assert msg2["data"]["step"] == 2
    finally:
        _remove_subscriber(tenant, queue)


@pytest.mark.unit
async def test_events_stream_endpoint_requires_auth(async_client: AsyncClient) -> None:
    """GET /api/v1/events/stream requires valid auth token."""
    resp = await async_client.get("/api/v1/events/stream")
    assert resp.status_code == 401


# =============================================================================
# 4. In-App Notifications Tests
# =============================================================================


@pytest.mark.unit
async def test_notifications_list_and_mark_read(async_client: AsyncClient) -> None:
    """GET /api/v1/notifications returns seeded list and marks read."""
    headers = {
        "Authorization": "Bearer alice@omnea.ai:tenant_test",
        "X-CSRF-Token": "csrf-test-token-xyz",
    }

    # 1. Retrieve notifications list
    resp = await async_client.get("/api/v1/notifications", headers=headers)
    assert resp.status_code == 200
    notifs = resp.json()
    assert isinstance(notifs, list)
    assert len(notifs) >= 4
    first_id = notifs[0]["id"]
    assert notifs[0]["is_read"] is False

    # 2. Mark first notification as read
    read_resp = await async_client.post(
        f"/api/v1/notifications/{first_id}/read",
        headers=headers,
    )
    assert read_resp.status_code == 200
    assert read_resp.json()["is_read"] is True

    # 3. Retrieve list again; verify is_read updated
    resp_updated = await async_client.get("/api/v1/notifications", headers=headers)
    assert resp_updated.status_code == 200
    updated_notifs = resp_updated.json()
    match = next(n for n in updated_notifs if n["id"] == first_id)
    assert match["is_read"] is True


# =============================================================================
# 5. Dashboard Metrics & Paginated Requests Tests
# =============================================================================


@pytest.mark.unit
async def test_dashboard_metrics_role_tailoring(async_client: AsyncClient) -> None:
    """GET /api/v1/dashboard/metrics returns tailored metric sections for all roles."""
    headers = {"Authorization": "Bearer alice@omnea.ai:tenant_test"}

    resp = await async_client.get("/api/v1/dashboard/metrics", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["tenant_id"] == "tenant_test"
    assert "requester" in data
    assert "approver" in data
    assert "finance_admin" in data
    assert "security_reviewer" in data
    assert "legal_reviewer" in data
    assert "tenant_admin" in data

    # Verify structured fields
    assert "total_requests" in data["requester"]
    assert "pending_approvals" in data["approver"]
    assert "total_budget" in data["finance_admin"]
    assert "open_reviews" in data["security_reviewer"]
    assert "active_contract_reviews" in data["legal_reviewer"]
    assert "security_score" in data["tenant_admin"]


@pytest.mark.unit
async def test_dashboard_requests_pagination(async_client: AsyncClient) -> None:
    """GET /api/v1/dashboard/requests returns paginated response schema."""
    headers = {"Authorization": "Bearer alice@omnea.ai:tenant_test"}

    resp = await async_client.get(
        "/api/v1/dashboard/requests?page=1&page_size=10&status=pending",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()

    assert "items" in data
    assert "total" in data
    assert data["page"] == 1
    assert data["page_size"] == 10
    assert "total_pages" in data


# =============================================================================
# 6. Dedicated Role Endpoints & RBAC Enforcement Tests
# =============================================================================


@pytest.mark.unit
async def test_dashboard_finance_rbac(async_client: AsyncClient) -> None:
    """GET /api/v1/dashboard/finance enforces 'finance:read' permission."""
    requester_headers = {"Authorization": "Bearer requester@omnea.ai:tenant_test"}
    finance_headers = {"Authorization": "Bearer finance@omnea.ai:tenant_test"}

    # Requester denied (403)
    resp_denied = await async_client.get("/api/v1/dashboard/finance", headers=requester_headers)
    assert resp_denied.status_code == 403
    assert "lacks required permission" in resp_denied.json()["detail"]

    # Finance Admin permitted (200)
    resp_ok = await async_client.get("/api/v1/dashboard/finance", headers=finance_headers)
    assert resp_ok.status_code == 200
    data = resp_ok.json()
    assert "total_budget" in data
    assert "spend_caps" in data
    assert "erp_sync_status" in data


@pytest.mark.unit
async def test_dashboard_security_rbac(async_client: AsyncClient) -> None:
    """GET /api/v1/dashboard/security enforces 'security:read' permission."""
    requester_headers = {"Authorization": "Bearer requester@omnea.ai:tenant_test"}
    sec_headers = {"Authorization": "Bearer sec_lead@omnea.ai:tenant_test"}

    # Requester denied (403)
    resp_denied = await async_client.get("/api/v1/dashboard/security", headers=requester_headers)
    assert resp_denied.status_code == 403

    # Security Reviewer permitted (200)
    resp_ok = await async_client.get("/api/v1/dashboard/security", headers=sec_headers)
    assert resp_ok.status_code == 200
    data = resp_ok.json()
    assert "open_reviews" in data
    assert "vendor_risk_scores" in data


@pytest.mark.unit
async def test_dashboard_legal_rbac(async_client: AsyncClient) -> None:
    """GET /api/v1/dashboard/legal enforces 'legal:read' permission."""
    requester_headers = {"Authorization": "Bearer requester@omnea.ai:tenant_test"}
    legal_headers = {"Authorization": "Bearer counsel@omnea.ai:tenant_test"}

    # Requester denied (403)
    resp_denied = await async_client.get("/api/v1/dashboard/legal", headers=requester_headers)
    assert resp_denied.status_code == 403

    # Legal Reviewer permitted (200)
    resp_ok = await async_client.get("/api/v1/dashboard/legal", headers=legal_headers)
    assert resp_ok.status_code == 200
    data = resp_ok.json()
    assert "active_contract_reviews" in data


@pytest.mark.unit
async def test_dashboard_admin_rbac(async_client: AsyncClient) -> None:
    """GET /api/v1/dashboard/admin enforces 'admin:read' permission."""
    requester_headers = {"Authorization": "Bearer requester@omnea.ai:tenant_test"}
    admin_headers = {"Authorization": "Bearer admin@omnea.ai:tenant_test"}

    # Requester denied (403)
    resp_denied = await async_client.get("/api/v1/dashboard/admin", headers=requester_headers)
    assert resp_denied.status_code == 403

    # Tenant Admin permitted (200)
    resp_ok = await async_client.get("/api/v1/dashboard/admin", headers=admin_headers)
    assert resp_ok.status_code == 200
    data = resp_ok.json()
    assert "active_users" in data
    assert "security_score" in data
