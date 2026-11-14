"""FastAPI main application entrypoint."""

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from api.routes.health import router as health_router
from api.routes.ingress import router as ingress_router
from api.routes.review import router as review_router
from workers.approval.slack_interactions import slack_router
from procure_core.config import get_settings
from procure_core.db import dispose_async_engine
from procure_core.log_config import get_logger, request_id_ctx, setup_logging, tenant_id_ctx

logger = get_logger(__name__)


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


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Inject request_id and tenant_id into contextvars for structured logging."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        req_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        settings = get_settings()
        tenant_id = request.headers.get("x-tenant-id") or settings.DEFAULT_TENANT_ID

        token_req = request_id_ctx.set(req_id)
        token_tenant = tenant_id_ctx.set(tenant_id)
        try:
            response = await call_next(request)
            response.headers["x-request-id"] = req_id
            response.headers["x-tenant-id"] = tenant_id
            return response
        finally:
            request_id_ctx.reset(token_req)
            tenant_id_ctx.reset(token_tenant)


def create_app() -> FastAPI:
    """Application factory for FastAPI."""
    settings = get_settings()
    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        description="AI-native procurement orchestration platform",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestContextMiddleware)

    app.include_router(health_router)
    app.include_router(ingress_router)
    app.include_router(review_router)
    app.include_router(slack_router)

    return app


app = create_app()
