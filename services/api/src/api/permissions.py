"""Authentication and route permission enforcement engine for API and admin endpoints."""

from __future__ import annotations

import re
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from procure_core.auth.rbac import RbacEngine
from procure_core.config import get_settings
from procure_core.db import get_async_session
from procure_core.log_config import tenant_id_ctx
from procure_core.security.tokens import decode_access_token

# =============================================================================
# 1. Canonical Route Permissions Registry & Public Routes
# =============================================================================

ROUTE_PERMISSIONS_REGISTRY: dict[tuple[str, str], str] = {
    # Stage 1: Auth & User Management Endpoints
    ("POST", "/auth/signup"): "public",
    ("POST", "/auth/verify-email"): "public",
    ("POST", "/auth/login"): "public",
    ("POST", "/auth/logout"): "user:read",
    ("POST", "/auth/refresh"): "public",
    ("POST", "/auth/forgot-password"): "public",
    ("POST", "/auth/reset-password"): "public",
    ("GET", "/auth/me"): "user:read",
    ("POST", "/auth/totp/setup"): "user:read",
    ("POST", "/auth/totp/verify"): "user:read",
    ("POST", "/auth/oidc/login"): "public",
    ("POST", "/invitations"): "invitations:create",
    ("POST", "/invitations/accept"): "public",
    ("POST", "/auth/users/{user_id}/role"): "roles:manage",
    ("POST", "/auth/users/{user_id}/revoke"): "users:manage",
    # Reviews
    ("GET", "/api/v1/reviews/tasks"): "review:read",
    ("GET", "/api/v1/reviews/tasks/{task_id}"): "review:read",
    ("POST", "/api/v1/reviews/tasks/{task_id}/resolve"): "review:resolve",
    ("POST", "/api/v1/reviews/tasks/{task_id}/unmerge"): "review:resolve",
    # Admin REST APIs
    ("GET", "/api/v1/admin/overview"): "admin:read",
    ("GET", "/api/v1/admin/audit-chain"): "admin:read",
    ("GET", "/api/v1/admin/outbox"): "admin:read",
    ("GET", "/api/v1/admin/erp-health"): "admin:read",
    ("GET", "/api/v1/admin/spend-caps"): "admin:read",
    # Admin Actions
    ("POST", "/api/v1/admin/actions/erp-repair"): "admin:write",
    ("POST", "/api/v1/admin/actions/policy-config"): "admin:write",
    # Admin HTML views
    ("GET", "/admin/dashboard"): "admin:read",
    ("GET", "/admin/entity-review"): "review:read",
    ("GET", "/admin/tprm-evidence"): "admin:read",
    ("GET", "/admin/erp-repairs"): "admin:read",
    ("GET", "/admin/policy-config"): "admin:read",
    ("GET", "/admin/integrations"): "admin:read",
    ("GET", "/admin/audit-search"): "admin:read",
    # Stage 2: Real-Time SSE, Notifications, and Role Dashboard Endpoints
    ("GET", "/api/v1/events/stream"): "user:read",
    ("POST", "/api/v1/events/ticket"): "user:read",
    ("GET", "/api/v1/notifications"): "user:read",
    ("POST", "/api/v1/notifications/{notification_id}/read"): "user:read",
    ("GET", "/api/v1/dashboard/metrics"): "user:read",
    ("GET", "/api/v1/dashboard/requests"): "user:read",
    ("GET", "/api/v1/dashboard/finance"): "finance:read",
    ("GET", "/api/v1/dashboard/security"): "security:read",
    ("GET", "/api/v1/dashboard/legal"): "legal:read",
    ("GET", "/api/v1/dashboard/admin"): "admin:read",
    # Stage 3: Requests & Approvals Endpoints
    ("GET", "/api/v1/requests/config/llm-status"): "requests:read",
    ("POST", "/api/v1/requests/intake/chat"): "requests:create",
    ("GET", "/api/v1/requests"): "requests:read",
    ("GET", "/api/v1/requests/{id}"): "requests:read",
    ("POST", "/api/v1/requests/{id}/comments"): "requests:create",
    ("GET", "/api/v1/approvals/pending"): "approvals:read",
    ("POST", "/api/v1/approvals/{step_id}/decide"): "approvals:decide",
    ("GET", "/api/v1/approvals/delegation"): "approvals:read",
    ("POST", "/api/v1/approvals/delegation"): "approvals:decide",
    # Stage 4: Vendors, Finance, and Legal Endpoints
    ("GET", "/api/v1/vendors"): "vendors:read",
    ("GET", "/api/v1/vendors/graph"): "vendors:read",
    ("GET", "/api/v1/vendors/{id}"): "vendors:read",
    ("POST", "/api/v1/vendors/merge"): "vendors:manage",
    ("POST", "/api/v1/vendors/{id}/unmerge"): "vendors:manage",
    ("POST", "/api/v1/vendors/{id}/research"): "vendors:read",
    ("GET", "/api/v1/finance/budgets"): "finance:read",
    ("POST", "/api/v1/finance/budgets"): "finance:manage",
    ("GET", "/api/v1/finance/reservations"): "finance:read",
    ("GET", "/api/v1/finance/spend-dashboard"): "finance:read",
    ("GET", "/api/v1/finance/policies"): "finance:read",
    ("POST", "/api/v1/finance/policies/draft"): "finance:manage",
    ("POST", "/api/v1/finance/policies/confirm"): "finance:manage",
    ("GET", "/api/v1/legal/contracts"): "legal:read",
    ("GET", "/api/v1/legal/contracts/{id}"): "legal:read",
    ("POST", "/api/v1/legal/contracts"): "legal:manage",
    ("POST", "/api/v1/legal/contracts/{id}/flag-clause"): "legal:manage",
    # Stage 5: Security Reviewer Workspace & TPRM Hardening Endpoints
    ("POST", "/api/v1/security/documents/upload"): "security:review",
    ("GET", "/api/v1/security/documents"): "security:read",
    ("GET", "/api/v1/security/documents/{doc_id}"): "security:read",
    ("GET", "/api/v1/security/documents/{doc_id}/pages/{page_number}"): "security:read",
    ("GET", "/api/v1/security/risk-register"): "security:read",
    ("POST", "/api/v1/security/documents/{doc_id}/quarantine"): "security:review",
    # Stage 6: Systems of Record ERP, Idempotent Saga, Reconciliation, Slack App, Notifications
    ("GET", "/api/v1/erp/config"): "admin:read",
    ("POST", "/api/v1/erp/config"): "admin:write",
    ("POST", "/api/v1/erp/test-connection"): "admin:read",
    ("GET", "/api/v1/erp/status"): "finance:read",
    ("GET", "/api/v1/erp/purchase-orders"): "finance:read",
    ("GET", "/api/v1/erp/saga-executions"): "finance:read",
    ("POST", "/api/v1/reconciliation/run"): "finance:manage",
    ("GET", "/api/v1/reconciliation/latest"): "finance:read",
    ("GET", "/api/v1/reconciliation/findings"): "finance:read",
    ("POST", "/api/v1/reconciliation/findings/{finding_id}/repair"): "finance:manage",
    ("GET", "/api/v1/slack/manifest"): "public",
    ("GET", "/api/v1/slack/oauth/start"): "admin:read",
    ("GET", "/api/v1/slack/oauth/callback"): "public",
    ("GET", "/api/v1/slack/status"): "admin:read",
    ("POST", "/api/v1/slack/identities/map"): "admin:write",
    ("GET", "/api/v1/notifications/preferences"): "user:read",
    ("PUT", "/api/v1/notifications/preferences"): "user:read",
    # Stage 7: Developer Platform, REST, Webhooks, API Keys, Audit Export, Agent Runs
    ("POST", "/api/v1/requests"): "requests:create",
    ("GET", "/api/v1/requests/{id}/decisions"): "approvals:read",
    ("GET", "/api/v1/vendors/{id}/relationships"): "vendors:read",
    ("POST", "/api/v1/vendors/{id}/flag"): "vendors:manage",
    ("GET", "/api/v1/documents"): "security:read",
    ("GET", "/api/v1/documents/{doc_id}"): "security:read",
    ("GET", "/api/v1/documents/{doc_id}/assessment"): "security:read",
    ("GET", "/api/v1/api-keys"): "admin:read",
    ("POST", "/api/v1/api-keys"): "admin:write",
    ("POST", "/api/v1/api-keys/{key_id}/revoke"): "admin:write",
    ("DELETE", "/api/v1/api-keys/{key_id}"): "admin:write",
    ("GET", "/api/v1/webhooks/endpoints"): "admin:read",
    ("POST", "/api/v1/webhooks/endpoints"): "admin:write",
    ("GET", "/api/v1/webhooks/endpoints/{endpoint_id}"): "admin:read",
    ("DELETE", "/api/v1/webhooks/endpoints/{endpoint_id}"): "admin:write",
    ("GET", "/api/v1/webhooks/deliveries"): "admin:read",
    ("POST", "/api/v1/webhooks/deliveries/{delivery_id}/redeliver"): "admin:write",
    ("GET", "/api/v1/audit/export"): "admin:read",
    ("GET", "/api/v1/agents/runs"): "user:read",
    ("GET", "/api/v1/agents/runs/{run_id}"): "user:read",
    # Public route declarations & Web Pages
    ("GET", "/"): "public",
    ("GET", "/app"): "public",
    ("GET", "/app/{full_path:path}"): "public",
    ("GET", "/static/{file_path:path}"): "public",
    ("GET", "/health"): "public",
    ("GET", "/status"): "public",
    ("GET", "/api/v1/status"): "public",
    ("GET", "/docs"): "public",
    ("GET", "/docs/oauth2-redirect"): "public",
    ("GET", "/redoc"): "public",
    ("GET", "/openapi.json"): "public",
    ("POST", "/api/v1/ingress/slack/events"): "public",
    ("POST", "/api/v1/ingress/slack/commands"): "public",
    ("POST", "/api/v1/ingress/teams/messages"): "public",
    ("POST", "/api/v1/ingress/teams"): "public",
    ("POST", "/api/v1/ingress/email/inbound"): "public",
    ("POST", "/api/v1/ingress/email"): "public",
    ("POST", "/api/v1/slack/interactive"): "public",
}

PUBLIC_ROUTES: set[str | tuple[str, str]] = {
    "/",
    "/app",
    "/health",
    "/status",
    "/api/v1/status",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
    "/openapi.json",
    "/api/v1/ingress/slack/events",
    "/api/v1/ingress/slack/commands",
    "/api/v1/ingress/teams/messages",
    "/api/v1/ingress/teams",
    "/api/v1/ingress/email/inbound",
    "/api/v1/ingress/email",
    "/api/v1/slack/interactive",
    "/api/v1/slack/manifest",
    "/api/v1/slack/oauth/callback",
    "/auth/signup",
    "/auth/verify-email",
    "/auth/login",
    "/auth/refresh",
    "/auth/forgot-password",
    "/auth/reset-password",
    "/auth/oidc/login",
    "/invitations/accept",
    ("GET", "/"),
    ("GET", "/app"),
    ("GET", "/app/{full_path:path}"),
    ("GET", "/static/{file_path:path}"),
    ("GET", "/health"),
    ("GET", "/status"),
    ("GET", "/api/v1/status"),
    ("GET", "/docs"),
    ("GET", "/redoc"),
    ("GET", "/openapi.json"),
    ("POST", "/api/v1/ingress/slack/events"),
    ("POST", "/api/v1/ingress/slack/commands"),
    ("POST", "/api/v1/ingress/teams/messages"),
    ("POST", "/api/v1/ingress/email/inbound"),
    ("POST", "/api/v1/slack/interactive"),
    ("POST", "/auth/signup"),
    ("POST", "/auth/verify-email"),
    ("POST", "/auth/login"),
    ("POST", "/auth/refresh"),
    ("POST", "/auth/forgot-password"),
    ("POST", "/auth/reset-password"),
    ("POST", "/auth/oidc/login"),
    ("POST", "/invitations/accept"),
}


def _match_path(pattern: str, path: str) -> bool:
    """Check if request path matches a registry pattern with optional {params} or {param:path}."""
    p1 = pattern.rstrip("/") or "/"
    p2 = path.rstrip("/") or "/"
    if p1 == p2:
        return True
    # Replace wildcard {param:path} with regex .*
    regex_pattern = re.sub(r"\{[a-zA-Z0-9_]+:path\}", r".*", p1)
    # Replace standard {param} with regex [^/]+
    regex_pattern = re.sub(r"\{[a-zA-Z0-9_]+\}", r"[^/]+", regex_pattern)
    regex = "^" + regex_pattern + "$"
    return bool(re.match(regex, p2))


def verify_route_declared_and_permitted(method: str, path: str) -> str:
    """Verify that a route (method, path) is declared and return required permission code.

    Denies undeclared routes with HTTP 403 Forbidden.
    """
    m = method.upper()
    clean_path = path.split("?")[0].rstrip("/") or "/"

    # 1. Public route check
    if clean_path in PUBLIC_ROUTES or (m, clean_path) in PUBLIC_ROUTES:
        return "public"
    for item in PUBLIC_ROUTES:
        if isinstance(item, tuple):
            pub_m, pub_p = item
            if pub_m == m and _match_path(pub_p, clean_path):
                return "public"
        elif isinstance(item, str) and _match_path(item, clean_path):
            return "public"

    # 2. Exact match in registry
    if (m, clean_path) in ROUTE_PERMISSIONS_REGISTRY:
        return ROUTE_PERMISSIONS_REGISTRY[(m, clean_path)]

    # 3. Parameterized pattern match in registry
    for (reg_m, reg_p), perm in ROUTE_PERMISSIONS_REGISTRY.items():
        if reg_m == m and _match_path(reg_p, clean_path):
            return perm

    # 4. Undeclared route -> 403 Forbidden
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Route '{m} {clean_path}' is not declared in permissions registry",
    )


# =============================================================================
# 2. AuthContext Model
# =============================================================================


class AuthContext(BaseModel):
    """Authenticated user context for API requests and dependencies."""

    user_id: str
    tenant_id: str
    roles: list[str] = Field(default_factory=list)
    permissions: set[str] = Field(default_factory=set)
    token: str = ""
    email: str = ""

    def __init__(self, **data: Any) -> None:
        if "email" not in data and "user_id" in data:
            data["email"] = data["user_id"]
        super().__init__(**data)


# =============================================================================
# 3. Token Extraction & Permission Dependency Factory
# =============================================================================


def _extract_token_from_request(request: Request) -> str:
    """Extract auth token from Authorization header, X-API-Key, query param, or cookies."""
    # 1. Check X-API-Key header
    api_key_hdr = request.headers.get("x-api-key")
    if api_key_hdr and api_key_hdr.strip():
        return api_key_hdr.strip()

    # 2. Check Authorization header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.strip():
        if not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid Authorization header",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = auth_header.removeprefix("Bearer ").strip()
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Empty Bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return token

    # Disallow raw JWT / credentials in URL query parameter for SSE stream
    q_token = request.query_params.get("token")
    if q_token and q_token.strip():
        if request.url.path.startswith("/api/v1/events/stream"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Passing authentication tokens in URL query parameter '?token=' is forbidden for "
                    "security. Use httpOnly cookies, single-use stream tickets, or Authorization header."
                ),
            )
        return q_token.strip()

    # Check cookies
    for cookie_key in ("token", "auth_token", "access_token"):
        c_token = request.cookies.get(cookie_key)
        if c_token and c_token.strip():
            return c_token.strip()

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid Authorization header",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _parse_token(token: str) -> tuple[str, str]:
    """Parse user identity and tenant from token payload."""
    if ":" in token:
        user_part, tenant_part = token.split(":", 1)
    elif "_" in token:
        parts = token.split("_")
        user_part = f"{parts[1]}@omnea.ai" if len(parts) > 1 else "user@omnea.ai"
        tenant_part = parts[2] if len(parts) > 2 else "default"
    else:
        user_part = f"{token}@omnea.ai"
        tenant_part = token
    return user_part, tenant_part


def require_permission(
    permission_code: str,
) -> Callable[..., Coroutine[Any, Any, AuthContext]]:
    """FastAPI dependency factory enforcing tenant authorization and RBAC permissions."""

    async def _dependency(
        request: Request,
        session: AsyncSession = Depends(get_async_session),
    ) -> AuthContext:
        token = _extract_token_from_request(request)

        # 0. API Key handling
        if token.startswith("omnea_"):
            from sqlalchemy import select

            from procure_core.developer.api_keys import (
                get_api_key_rate_limiter,
                hash_api_key,
            )
            from procure_core.models.developer import ApiKeyModel

            k_hash = hash_api_key(token)
            stmt = select(ApiKeyModel).where(ApiKeyModel.key_hash == k_hash)
            res = await session.execute(stmt)
            key_row = res.scalar_one_or_none()
            if not key_row:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid API key",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if key_row.revoked_at is not None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="API key has been revoked",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            now_utc = datetime.now(UTC)
            if key_row.expires_at is not None and key_row.expires_at < now_utc:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="API key has expired",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # Rate limit checking per-key and per-tenant
            limiter = get_api_key_rate_limiter()
            allowed, _remaining, retry_after = limiter.check_and_consume(
                key_hash=key_row.key_hash,
                tenant_id=key_row.tenant_id,
                limit_per_minute=key_row.rate_limit_per_minute,
            )
            if not allowed:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded for API key or tenant",
                    headers={"Retry-After": str(retry_after)},
                )

            # Tenant validation if client passed X-Tenant-ID
            x_tenant_id = request.headers.get("x-tenant-id")
            if x_tenant_id and x_tenant_id != key_row.tenant_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Forbidden: API key tenant '{key_row.tenant_id}' does not match requested tenant '{x_tenant_id}'",
                )

            # Scope enforcement
            key_scopes = set(key_row.scopes or [])
            has_perm = (
                "*" in key_scopes
                or permission_code in key_scopes
                or (permission_code == "requests:create" and "requests:write" in key_scopes)
                or (
                    permission_code == "requests:read"
                    and ("requests:write" in key_scopes or "requests:create" in key_scopes)
                )
                or (permission_code == "vendors:read" and "vendors:manage" in key_scopes)
                or (permission_code == "approvals:read" and "approvals:decide" in key_scopes)
                or (permission_code == "documents:read" and "documents:review" in key_scopes)
                or (
                    permission_code == "security:read"
                    and any(s.startswith("documents:") for s in key_scopes)
                )
                or (
                    permission_code == "finance:read"
                    and any(
                        s.startswith("erp:") or s.startswith("reconciliation:") for s in key_scopes
                    )
                )
                or (permission_code == "admin:read" and "webhooks:read" in key_scopes)
                or (permission_code == "admin:write" and "webhooks:manage" in key_scopes)
                or permission_code in ("user:read", "public")
            )
            if not has_perm:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Forbidden: API key lacks required scope '{permission_code}'",
                )

            key_row.last_used_at = now_utc
            await session.flush()

            # Set verified tenant in context and database session
            if not key_row.tenant_id or not key_row.tenant_id.strip():
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="API key missing valid tenant_id claim",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            tenant_id_ctx.set(key_row.tenant_id)

            # Bind tenant to session for PostgreSQL Row Level Security (dual-wall enforcement)
            await session.execute(
                text("SELECT set_config('app.tenant_id', :t, true);"),
                {"t": key_row.tenant_id},
            )

            return AuthContext(
                user_id=f"apikey:{key_row.name}",
                tenant_id=key_row.tenant_id,
                roles=["api_key"],
                permissions=key_scopes,
                token=token,
                email=f"apikey-{key_row.key_prefix}@omnea.ai",
            )

        # 1. Parse token (JWT or test token)
        email = ""
        token_roles: list[str] = []
        if token.count(".") == 2:
            try:
                payload = decode_access_token(token)
                user_id = str(payload.get("sub", ""))
                token_tenant = str(payload.get("tenant_id") or "").strip()
                if not token_tenant:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Token missing verified tenant_id claim",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                tenant_id_ctx.set(token_tenant)
                email = str(payload.get("email", user_id))
                token_roles = list(payload.get("roles", []))
                session_id = payload.get("session_id")
                if session_id:
                    sess_res = await session.execute(
                        text("SELECT is_revoked, expires_at FROM sessions WHERE id = :sid"),
                        {"sid": session_id},
                    )
                    sess_row = sess_res.mappings().first()
                    if sess_row and (
                        sess_row["is_revoked"]
                        or (sess_row["expires_at"] and datetime.now(UTC) > sess_row["expires_at"])
                    ):
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Session has been revoked or expired",
                            headers={"WWW-Authenticate": "Bearer"},
                        )
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Invalid or expired token: {e}",
                    headers={"WWW-Authenticate": "Bearer"},
                ) from e
        else:
            settings = get_settings()
            if settings.APP_ENV not in ("test", "development"):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token format: Cryptographic JWT required",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            user_id, token_tenant = _parse_token(token)
            token_tenant = token_tenant.strip()
            if not token_tenant:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token missing verified tenant_id claim",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            tenant_id_ctx.set(token_tenant)
            email = user_id

        # 2. Enforce tenant isolation: tenant comes ONLY from the verified token claim.
        x_tenant_id = request.headers.get("x-tenant-id")
        if x_tenant_id and x_tenant_id != token_tenant:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Forbidden: Token tenant '{token_tenant}' does not match "
                    f"requested tenant '{x_tenant_id}'"
                ),
            )

        effective_tenant = token_tenant  # Tenant strictly from verified token claim
        tenant_id_ctx.set(effective_tenant)

        # 3. Evaluate RBAC engine against database and registered role assignments
        try:
            roles = await RbacEngine.resolve_roles(
                session=session,
                tenant_id=effective_tenant,
                user_id=user_id,
            )
        except Exception:
            roles = set()

        if token_roles:
            roles.update(token_roles)

        # Strict deny-by-default permission check
        has_perm = permission_code in roles or any(
            RbacEngine._role_grants_permission(role, permission_code) for role in roles
        )

        if not has_perm:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: User '{user_id}' lacks required permission '{permission_code}'",
            )

        # Set session RLS tenant context
        await session.execute(
            text("SELECT set_config('app.tenant_id', :t, true);"),
            {"t": effective_tenant},
        )

        return AuthContext(
            user_id=user_id,
            tenant_id=effective_tenant,
            roles=list(roles) if roles else ["user"],
            permissions={permission_code},
            token=token,
            email=email or user_id,
        )

    return _dependency
