"""Unit tests for API route permissions registry, verification, and auth dependencies."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.permissions import (
    PUBLIC_ROUTES,
    ROUTE_PERMISSIONS_REGISTRY,
    AuthContext,
    require_permission,
    verify_route_declared_and_permitted,
)
from procure_core.auth.rbac import RbacEngine


@pytest.fixture(autouse=True)
def setup_rbac_test_roles() -> Iterator[None]:
    RbacEngine.clear_fallback_roles()
    RbacEngine.assign_fallback_role("tenant_corp", "admin@omnea.ai", "admin")
    yield
    RbacEngine.clear_fallback_roles()


def _mock_session() -> AsyncMock:
    session = AsyncMock()
    res = MagicMock()
    res.mappings.return_value = []
    session.execute.return_value = res
    return session


@pytest.mark.unit
def test_route_permissions_registry_contains_canonical_routes() -> None:
    """Verify registry contains all canonical endpoints."""
    assert "/health" in PUBLIC_ROUTES or ("GET", "/health") in PUBLIC_ROUTES
    assert ("GET", "/api/v1/admin/overview") in ROUTE_PERMISSIONS_REGISTRY
    assert ("GET", "/api/v1/admin/audit-chain") in ROUTE_PERMISSIONS_REGISTRY
    assert ("GET", "/api/v1/admin/outbox") in ROUTE_PERMISSIONS_REGISTRY
    assert ("GET", "/api/v1/admin/erp-health") in ROUTE_PERMISSIONS_REGISTRY
    assert ("GET", "/api/v1/admin/spend-caps") in ROUTE_PERMISSIONS_REGISTRY
    assert ("POST", "/api/v1/admin/actions/erp-repair") in ROUTE_PERMISSIONS_REGISTRY
    assert ("POST", "/api/v1/admin/actions/policy-config") in ROUTE_PERMISSIONS_REGISTRY
    assert ("GET", "/admin/dashboard") in ROUTE_PERMISSIONS_REGISTRY
    assert ("GET", "/admin/integrations") in ROUTE_PERMISSIONS_REGISTRY
    assert ("POST", "/auth/signup") in ROUTE_PERMISSIONS_REGISTRY
    assert ("POST", "/auth/login") in ROUTE_PERMISSIONS_REGISTRY
    assert ("POST", "/invitations") in ROUTE_PERMISSIONS_REGISTRY
    assert ("POST", "/invitations/accept") in ROUTE_PERMISSIONS_REGISTRY
    assert ("POST", "/auth/users/{user_id}/role") in ROUTE_PERMISSIONS_REGISTRY
    assert ("POST", "/auth/users/{user_id}/revoke") in ROUTE_PERMISSIONS_REGISTRY


@pytest.mark.unit
def test_all_fastapi_routes_are_declared_in_registry() -> None:
    """Enforce that every mounted FastAPI route is declared in the permissions registry."""
    from api.main import create_app

    app = create_app()

    def _extract_routes(router_or_app: Any, prefix: str = "") -> list[tuple[str, str]]:
        routes: list[tuple[str, str]] = []
        for r in getattr(router_or_app, "routes", []):
            if type(r).__name__ == "_IncludedRouter":
                inc_prefix = getattr(r.include_context, "prefix", "")
                routes.extend(_extract_routes(r.original_router, prefix + inc_prefix))
            elif hasattr(r, "methods") and hasattr(r, "path"):
                full_path = prefix + r.path
                for method in r.methods:
                    if method not in ("HEAD", "OPTIONS"):
                        routes.append((method, full_path))
        return routes

    all_routes = _extract_routes(app)
    assert len(all_routes) >= 20, f"Expected at least 20 routes, got {len(all_routes)}"

    for method, path in all_routes:
        perm = verify_route_declared_and_permitted(method, path)
        assert perm, f"Route {method} {path} has empty permission declaration"


@pytest.mark.unit
def test_verify_route_declared_and_permitted() -> None:
    """Declared routes return their permission, undeclared routes raise 403."""
    # Public route
    assert verify_route_declared_and_permitted("GET", "/health") == "public"

    # Declared admin overview
    assert verify_route_declared_and_permitted("GET", "/api/v1/admin/overview") == "admin:read"

    # Declared action
    assert (
        verify_route_declared_and_permitted("POST", "/api/v1/admin/actions/erp-repair")
        == "admin:write"
    )

    # Parameterized review route
    assert (
        verify_route_declared_and_permitted(
            "POST", "/api/v1/reviews/tasks/123e4567-e89b-12d3-a456-426614174000/resolve"
        )
        == "review:resolve"
    )

    # Undeclared route raises 403 Forbidden
    with pytest.raises(HTTPException) as exc_info:
        verify_route_declared_and_permitted("GET", "/api/v1/unknown/shadow-route")
    assert exc_info.value.status_code == 403
    assert "not declared" in exc_info.value.detail


@pytest.mark.unit
def test_auth_context_model() -> None:
    """AuthContext model instantiation and defaults."""
    ctx = AuthContext(
        user_id="alice@omnea.ai",
        tenant_id="tenant_alpha",
        roles=["admin"],
        permissions={"admin:read"},
        token="admin_token",
    )
    assert ctx.user_id == "alice@omnea.ai"
    assert ctx.tenant_id == "tenant_alpha"
    assert "admin" in ctx.roles
    assert "admin:read" in ctx.permissions
    assert ctx.token == "admin_token"
    assert ctx.email == "alice@omnea.ai"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_require_permission_missing_token_raises_401() -> None:
    """Missing Authorization header and cookies raises 401."""
    dep = require_permission("admin:read")
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/admin/overview",
        "headers": [],
        "query_string": b"",
    }
    request = Request(scope)
    with pytest.raises(HTTPException) as exc_info:
        await dep(request, session=_mock_session())
    assert exc_info.value.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_require_permission_extracts_from_query_param() -> None:
    """Token provided in query parameter is extracted and validated."""
    dep = require_permission("admin:read")
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/admin/dashboard",
        "headers": [],
        "query_string": b"token=admin@omnea.ai:tenant_corp",
    }
    request = Request(scope)
    ctx = await dep(request, session=_mock_session())
    assert ctx.user_id == "admin@omnea.ai"
    assert ctx.tenant_id == "tenant_corp"
    assert "admin" in ctx.roles


@pytest.mark.unit
@pytest.mark.asyncio
async def test_require_permission_tenant_mismatch_raises_403() -> None:
    """x-tenant-id header mismatching token tenant raises 403 Forbidden."""
    dep = require_permission("admin:read")
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/admin/overview",
        "headers": [
            (b"authorization", b"Bearer admin@omnea.ai:tenant_a"),
            (b"x-tenant-id", b"tenant_b"),
        ],
        "query_string": b"",
    }
    request = Request(scope)
    with pytest.raises(HTTPException) as exc_info:
        await dep(request, session=_mock_session())
    assert exc_info.value.status_code == 403
    assert "does not match" in exc_info.value.detail
