"""Unit and integration tests for Admin REST endpoints, HTML views, and CSRF protection."""

from __future__ import annotations

import os
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from api.routes.admin import generate_csrf_token
from procure_core.auth.rbac import RbacEngine


@pytest.fixture(autouse=True)
def setup_admin_test_roles() -> Iterator[None]:
    RbacEngine.clear_fallback_roles()
    RbacEngine.assign_fallback_role("*", "admin", "admin")
    RbacEngine.assign_fallback_role("*", "admin@omnea.ai", "admin")
    yield
    RbacEngine.clear_fallback_roles()


@pytest.mark.unit
async def test_admin_endpoints_require_auth(async_client: AsyncClient) -> None:
    """Missing auth header/token returns 401 Unauthorized."""
    resp = await async_client.get("/api/v1/admin/overview")
    assert resp.status_code == 401
    assert "Missing or invalid Authorization header" in resp.json()["detail"]

    resp_html = await async_client.get("/admin/dashboard")
    assert resp_html.status_code == 401


@pytest.mark.unit
async def test_admin_endpoints_permission_check(async_client: AsyncClient) -> None:
    """Non-admin user token returns 403 Forbidden."""
    resp = await async_client.get(
        "/api/v1/admin/overview",
        headers={"Authorization": "Bearer requester_alice_tenant1"},
    )
    assert resp.status_code == 403
    assert "lacks required permission" in resp.json()["detail"]


@pytest.mark.unit
async def test_admin_overview_api(async_client: AsyncClient) -> None:
    """Admin overview returns tenant procurement metrics and KPIs."""
    headers = {"Authorization": "Bearer admin:test_tenant"}
    resp = await async_client.get("/api/v1/admin/overview", headers=headers)
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["tenant_id"] == "test_tenant"
    assert "total_requests" in data
    assert "pending_entity_reviews" in data
    assert "outbox_queue_depth" in data
    assert "outbox_dlq_count" in data
    assert "total_budget" in data
    assert "committed_spend" in data
    assert "remaining_budget" in data
    assert "audit_chain_valid" in data


@pytest.mark.unit
async def test_admin_audit_chain_api(async_client: AsyncClient) -> None:
    """Admin audit chain endpoint returns verification results."""
    headers = {"Authorization": "Bearer admin:test_tenant"}
    resp = await async_client.get("/api/v1/admin/audit-chain", headers=headers)
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["tenant_id"] == "test_tenant"
    assert "is_valid" in data
    assert "total_entries" in data


@pytest.mark.unit
async def test_admin_outbox_api(async_client: AsyncClient) -> None:
    """Admin outbox endpoint returns queue depth and DLQ records."""
    headers = {"Authorization": "Bearer admin:test_tenant"}
    resp = await async_client.get("/api/v1/admin/outbox", headers=headers)
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["tenant_id"] == "test_tenant"
    assert "queue_depth" in data
    assert "dlq_count" in data
    assert isinstance(data["items"], list)


@pytest.mark.unit
async def test_admin_erp_health_api(async_client: AsyncClient) -> None:
    """Admin ERP health endpoint returns circuit breakers and token bucket status."""
    headers = {"Authorization": "Bearer admin:test_tenant"}
    resp = await async_client.get("/api/v1/admin/erp-health", headers=headers)
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert "overall_status" in data
    assert "circuit_breakers" in data
    assert "sap" in data["circuit_breakers"]
    assert "netsuite" in data["circuit_breakers"]
    assert "token_buckets" in data


@pytest.mark.unit
async def test_admin_spend_caps_api(async_client: AsyncClient) -> None:
    """Admin spend caps endpoint returns budget vs committed spend."""
    headers = {"Authorization": "Bearer admin:test_tenant"}
    resp = await async_client.get("/api/v1/admin/spend-caps", headers=headers)
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["tenant_id"] == "test_tenant"
    assert "total_budget" in data
    assert "committed_spend" in data
    assert "remaining_budget" in data
    assert "spend_caps" in data


@pytest.mark.unit
async def test_admin_action_erp_repair_json(async_client: AsyncClient) -> None:
    """Admin action erp-repair executes repair and returns success."""
    headers = {"Authorization": "Bearer admin:test_tenant"}
    payload = {"connector": "sap", "action": "reset_circuit_breaker"}
    resp = await async_client.post(
        "/api/v1/admin/actions/erp-repair",
        json=payload,
        headers=headers,
    )
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["status"] == "success"
    assert "reset_circuit_breaker" in data["message"].lower()


@pytest.mark.unit
async def test_admin_action_policy_config_json(async_client: AsyncClient) -> None:
    """Admin action policy-config updates policy rules and returns success."""
    headers = {"Authorization": "Bearer admin:test_tenant"}
    payload = {
        "policy_name": "procurement_v2",
        "rules": {"max_auto_approve": 7500, "currency": "USD"},
    }
    resp = await async_client.post(
        "/api/v1/admin/actions/policy-config",
        json=payload,
        headers=headers,
    )
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["status"] == "success"
    assert data["policy_name"] == "procurement_v2"


@pytest.mark.unit
async def test_admin_action_csrf_protection(async_client: AsyncClient) -> None:
    """Form submissions enforce CSRF token validity."""
    headers = {"Authorization": "Bearer admin:test_tenant"}

    # 1. Invalid CSRF token -> 403 Forbidden
    resp_invalid = await async_client.post(
        "/api/v1/admin/actions/erp-repair",
        data={
            "csrf_token": "invalid_csrf_token_signature",
            "connector": "sap",
            "action": "refill_tokens",
        },
        headers=headers,
    )
    assert resp_invalid.status_code == 403
    assert "CSRF" in resp_invalid.json()["detail"]

    # 2. Valid CSRF token -> 303 Redirect to HTML view
    valid_token = generate_csrf_token("test_tenant", "admin")
    resp_valid = await async_client.post(
        "/api/v1/admin/actions/erp-repair",
        data={
            "csrf_token": valid_token,
            "connector": "sap",
            "action": "refill_tokens",
        },
        headers=headers,
        follow_redirects=False,
    )
    assert resp_valid.status_code == 303
    assert "/admin/erp-repairs" in resp_valid.headers["location"]


@pytest.mark.unit
async def test_admin_html_views(async_client: AsyncClient) -> None:
    """All admin HTML views render with 200 OK and valid layout."""
    token = "admin:test_tenant"

    views = [
        "/admin/dashboard",
        "/admin/entity-review",
        "/admin/tprm-evidence",
        "/admin/erp-repairs",
        "/admin/policy-config",
        "/admin/audit-search",
    ]

    for view_url in views:
        resp = await async_client.get(f"{view_url}?token={token}")
        assert resp.status_code == 200, resp.json()
        assert "text/html" in resp.headers["content-type"]
        assert "Omnea Procure Admin" in resp.text


@pytest.mark.unit
async def test_admin_integrations_view_safe_degradation(async_client: AsyncClient) -> None:
    """Critical rule: missing external credentials display 'Not connected' with setup guide."""
    token = "admin:test_tenant"

    # Simulate environment with missing ERP & external credentials
    env_clean = {
        "ERP_API_KEY": "",
        "SAP_CLIENT_ID": "",
        "NETSUITE_TOKEN": "",
        "SLACK_BOT_TOKEN": "",
    }
    with patch.dict(os.environ, env_clean):
        resp = await async_client.get(f"/admin/integrations?token={token}")
        assert resp.status_code == 200, resp.json()
        assert "text/html" in resp.headers["content-type"]
        # Check for Not Connected badge and Setup Guide
        assert "Not connected" in resp.text
        assert "Setup Guide" in resp.text
        assert "SAP & NetSuite ERP" in resp.text
        assert "Slack Bot & Webhooks" in resp.text
