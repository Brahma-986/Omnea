"""FastAPI main application entrypoint."""

import mimetypes
import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import FileResponse, HTMLResponse, JSONResponse

from api.permissions import verify_route_declared_and_permitted
from api.rate_limiting import RateLimitMiddleware
from api.routes.admin import router as admin_router
from api.routes.agent_runs import router as agent_runs_router
from api.routes.api_keys import router as api_keys_router
from api.routes.approvals import router as approvals_router
from api.routes.audit import router as audit_router
from api.routes.auth import router as auth_router
from api.routes.dashboard import router as dashboard_router
from api.routes.documents import router as documents_router
from api.routes.erp import router as erp_router
from api.routes.events import router as events_router
from api.routes.finance import router as finance_router
from api.routes.health import router as health_router
from api.routes.ingress import router as ingress_router
from api.routes.invitations import router as invitations_router
from api.routes.legal import router as legal_router
from api.routes.notifications import router as notifications_router
from api.routes.reconciliation import router as reconciliation_router
from api.routes.requests import router as requests_router
from api.routes.review import router as review_router
from api.routes.security import router as security_router
from api.routes.slack import router as slack_api_router
from api.routes.status import router as status_router
from api.routes.vendors import router as vendors_router
from api.routes.webhooks import router as webhooks_router
from procure_agents.llm.spend_cap import LLMSpendBudgetExceededError
from procure_core.config import get_settings
from procure_core.db import dispose_async_engine
from procure_core.log_config import get_logger, request_id_ctx, setup_logging, tenant_id_ctx
from procure_core.security.tokens import decode_access_token
from workers.approval.slack_interactions import slack_router

logger = get_logger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML_PATH = STATIC_DIR / "index.html"
SWAGGER_INDEX_PATH = STATIC_DIR / "swagger" / "index.html"

# Explicitly ensure correct MIME types for static assets under strict CSP / nosniff
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/png", ".png")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown lifecycle."""
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)
    logger.info(
        f"Starting {settings.APP_NAME} in {settings.APP_ENV} mode",
        extra={"env": settings.APP_ENV},
    )
    yield
    logger.info("Shutting down API service, disposing database connections")
    await dispose_async_engine()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject strict Content Security Policy (CSP) and anti-clickjacking headers."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


CSRF_EXEMPT_PATHS: set[str] = {
    "/auth/login",
    "/auth/signup",
    "/auth/verify-email",
    "/auth/refresh",
    "/auth/forgot-password",
    "/auth/reset-password",
    "/auth/oidc/login",
    "/invitations/accept",
    "/api/v1/events/ticket",
}


def _is_csrf_exempt(path: str) -> bool:
    """Check whether a path is exempt from CSRF validation."""
    clean = path.rstrip("/") or "/"
    if clean in CSRF_EXEMPT_PATHS:
        return True
    if clean.startswith("/api/v1/ingress/") or clean == "/api/v1/slack/interactive":
        return True
    return False


class CSRFValidationMiddleware(BaseHTTPMiddleware):
    """Validate X-CSRF-Token header on mutating requests."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            path = request.url.path.rstrip("/") or "/"
            if not _is_csrf_exempt(path):
                # Programmatic API Key requests are exempt from CSRF validation
                auth_hdr = request.headers.get("authorization", "")
                if request.headers.get("x-api-key") or auth_hdr.startswith("Bearer omnea_"):
                    return await call_next(request)

                csrf_token = request.headers.get("x-csrf-token")
                if not csrf_token or not csrf_token.strip():
                    # Allow admin actions form posts that validate signed token in handler
                    if not path.startswith("/api/v1/admin/actions/"):
                        return JSONResponse(
                            status_code=status.HTTP_403_FORBIDDEN,
                            content={"detail": "CSRF token missing or invalid"},
                        )
        return await call_next(request)


class RouteRegistryEnforcementMiddleware(BaseHTTPMiddleware):
    """Enforce deny-by-default route permissions registry at runtime."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method == "OPTIONS":
            return await call_next(request)
        try:
            verify_route_declared_and_permitted(request.method, request.url.path)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
            )
        return await call_next(request)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Inject request_id and tenant_id into contextvars for structured logging."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        req_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        token_req = request_id_ctx.set(req_id)
        # Pre-extract verified tenant from JWT or cookie if provided
        initial_tenant: str | None = None
        auth_hdr = request.headers.get("Authorization") or request.headers.get("authorization")
        if auth_hdr and auth_hdr.startswith("Bearer "):
            raw_t = auth_hdr.removeprefix("Bearer ").strip()
            if raw_t.count(".") == 2:
                try:
                    payload = decode_access_token(raw_t)
                    t = payload.get("tenant_id")
                    if t and isinstance(t, str) and t.strip():
                        initial_tenant = t.strip()
                except Exception:
                    pass
        elif not initial_tenant:
            for c_key in ("access_token", "auth_token", "token"):
                c_val = request.cookies.get(c_key)
                if c_val and c_val.count(".") == 2:
                    try:
                        payload = decode_access_token(c_val)
                        t = payload.get("tenant_id")
                        if t and isinstance(t, str) and t.strip():
                            initial_tenant = t.strip()
                            break
                    except Exception:
                        pass

        token_tenant = tenant_id_ctx.set(initial_tenant)
        try:
            response = await call_next(request)
            response.headers["x-request-id"] = req_id
            verified_tenant = tenant_id_ctx.get()
            if verified_tenant:
                response.headers["x-tenant-id"] = verified_tenant
            return response
        finally:
            request_id_ctx.reset(token_req)
            tenant_id_ctx.reset(token_tenant)


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Intercept mutating API requests with Idempotency-Key header for duplicate suppression."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method in ("POST", "PUT", "PATCH") and request.url.path.startswith("/api/v1/"):
            idem_key = request.headers.get("idempotency-key")
            if idem_key and idem_key.strip():
                clean_key = idem_key.strip()
                tenant_id = tenant_id_ctx.get() or request.headers.get("x-tenant-id")
                if not tenant_id:
                    return await call_next(request)
                import json
                from datetime import datetime, timedelta

                from sqlalchemy import and_, select
                from starlette.concurrency import iterate_in_threadpool

                from procure_core.db import get_async_session_factory
                from procure_core.models.developer import ApiIdempotencyRecordModel

                factory = get_async_session_factory()
                try:
                    async with factory() as session:
                        stmt = select(ApiIdempotencyRecordModel).where(
                            and_(
                                ApiIdempotencyRecordModel.tenant_id == tenant_id,
                                ApiIdempotencyRecordModel.idempotency_key == clean_key,
                                ApiIdempotencyRecordModel.expires_at > datetime.now(UTC),
                            )
                        )
                        cached = (await session.execute(stmt)).scalar_one_or_none()
                        if cached:
                            return JSONResponse(
                                status_code=cached.response_status_code,
                                content=cached.response_body,
                                headers={"X-Cache": "HIT-IDEMPOTENT"},
                            )
                except Exception:
                    pass

                response = await call_next(request)

                if 200 <= response.status_code < 300 and "application/json" in response.headers.get(
                    "content-type", ""
                ):
                    try:
                        body_iter = getattr(response, "body_iterator", None)
                        if body_iter is not None:
                            resp_body = [section async for section in body_iter]
                            response.body_iterator = iterate_in_threadpool(iter(resp_body))  # type: ignore[attr-defined]
                            body_text = b"".join(resp_body).decode("utf-8")
                            body_json = json.loads(body_text)

                            async with factory() as session:
                                rec = ApiIdempotencyRecordModel(
                                    tenant_id=tenant_id,
                                    idempotency_key=clean_key,
                                    request_path=request.url.path,
                                    request_method=request.method,
                                    response_status_code=response.status_code,
                                    response_body=body_json,
                                    expires_at=datetime.now(UTC) + timedelta(hours=24),
                                )
                                session.add(rec)
                                await session.commit()
                    except Exception:
                        pass
                return response

        return await call_next(request)


def _status_to_error_code(status_code: int) -> str:
    codes = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "UNPROCESSABLE_ENTITY",
        429: "TOO_MANY_REQUESTS",
        500: "INTERNAL_SERVER_ERROR",
    }
    return codes.get(status_code, f"ERROR_{status_code}")


def create_app() -> FastAPI:
    """Application factory for FastAPI."""
    settings = get_settings()
    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        description="AI-native procurement orchestration platform",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.exception_handler(HTTPException)
    async def custom_http_exception_handler(_request: Request, exc: HTTPException) -> Response:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "error": {
                    "code": _status_to_error_code(exc.status_code),
                    "message": str(exc.detail),
                    "status_code": exc.status_code,
                    "details": None,
                },
            },
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(LLMSpendBudgetExceededError)
    async def spend_budget_exception_handler(
        _request: Request, exc: LLMSpendBudgetExceededError
    ) -> Response:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "detail": str(exc),
                "error": {
                    "code": "SPEND_CAP_EXCEEDED",
                    "message": str(exc),
                    "status_code": status.HTTP_429_TOO_MANY_REQUESTS,
                    "details": None,
                },
            },
            headers={"Retry-After": "3600"},
        )

    # Support both CORS_ORIGINS and CORS_ALLOWED_ORIGINS as well as FRONTEND_URL and VERCEL_URL
    cors_env = (
        os.getenv("CORS_ORIGINS")
        or os.getenv("CORS_ALLOWED_ORIGINS")
        or settings.CORS_ORIGINS
        or ""
    )
    frontend_url = (os.getenv("FRONTEND_URL") or settings.FRONTEND_URL or "").strip()
    vercel_url = os.getenv("VERCEL_URL", "").strip()
    if vercel_url and not vercel_url.startswith("http"):
        vercel_url = f"https://{vercel_url}"

    allowed_origins = [
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8000",
    ]
    if frontend_url and frontend_url not in allowed_origins:
        allowed_origins.append(frontend_url)
    if vercel_url and vercel_url not in allowed_origins:
        allowed_origins.append(vercel_url)
    if cors_env:
        for origin in cors_env.split(","):
            clean_origin = origin.strip()
            if clean_origin and clean_origin not in allowed_origins:
                allowed_origins.append(clean_origin)

    # Middleware stack (wrapped bottom-up, executed top-down on request)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_origin_regex=r"^https://.*\.vercel\.app$",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        allow_headers=["*"],
    )
    app.add_middleware(IdempotencyMiddleware)
    app.add_middleware(CSRFValidationMiddleware)
    app.add_middleware(RouteRegistryEnforcementMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)  # Outermost wrapper for all responses

    # API Routers
    app.include_router(health_router)
    app.include_router(status_router)
    app.include_router(auth_router)
    app.include_router(invitations_router)
    app.include_router(ingress_router)
    app.include_router(review_router)
    app.include_router(admin_router)
    app.include_router(slack_router)
    app.include_router(events_router)
    app.include_router(notifications_router)
    app.include_router(dashboard_router)
    app.include_router(requests_router)
    app.include_router(approvals_router)
    app.include_router(vendors_router)
    app.include_router(finance_router)
    app.include_router(legal_router)
    app.include_router(security_router)
    app.include_router(erp_router)
    app.include_router(reconciliation_router)
    app.include_router(slack_api_router)
    app.include_router(api_keys_router)
    app.include_router(webhooks_router)
    app.include_router(audit_router)
    app.include_router(agent_runs_router)
    app.include_router(documents_router)

    # Mount static assets
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Self-hosted Swagger UI (same-origin, strict CSP compliant, zero inline script)
    @app.get("/docs", include_in_schema=False)
    async def serve_swagger_docs(_request: Request) -> Response:
        if SWAGGER_INDEX_PATH.exists():
            return FileResponse(str(SWAGGER_INDEX_PATH), media_type="text/html")
        return HTMLResponse(
            "<!DOCTYPE html><html><head><title>Docs</title></head><body>Docs not found</body></html>",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # SPA Web Page routes
    @app.get("/", include_in_schema=False)
    @app.get("/app", include_in_schema=False)
    @app.get("/app/{full_path:path}", include_in_schema=False)
    async def serve_spa_index(_request: Request, full_path: str = "") -> Response:
        _ = full_path
        if INDEX_HTML_PATH.exists():
            return FileResponse(str(INDEX_HTML_PATH), media_type="text/html")
        html = (
            "<!DOCTYPE html><html><head><title>Omnea</title></head>"
            "<body><div id='app-root'></div></body></html>"
        )
        return HTMLResponse(html, media_type="text/html")

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        openapi_schema.setdefault("components", {})
        openapi_schema["components"]["securitySchemes"] = {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "Enter JWT Bearer token or API key (omnea_live_...)",
            },
            "ApiKeyAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "X-API-Key",
                "description": "API Key authentication header",
            },
        }
        openapi_schema["security"] = [{"BearerAuth": []}, {"ApiKeyAuth": []}]
        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    return app


app = create_app()
