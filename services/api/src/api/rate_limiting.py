"""Sliding-window global, per-tenant, and per-IP rate limiting middleware."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any

from fastapi import Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from procure_core.config import Settings, get_settings
from procure_core.log_config import get_logger, tenant_id_ctx

logger = get_logger(__name__)


class SlidingWindowRateLimiter:
    """Thread-safe sliding window (60 seconds) rate limiter."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._lock = threading.Lock()
        self._global_timestamps: list[float] = []
        self._tenant_timestamps: dict[str, list[float]] = defaultdict(list)
        self._ip_timestamps: dict[str, list[float]] = defaultdict(list)

    @property
    def global_limit(self) -> int:
        return self.settings.RATE_LIMIT_GLOBAL_PER_MIN

    @property
    def tenant_limit(self) -> int:
        return self.settings.RATE_LIMIT_TENANT_PER_MIN

    @property
    def ip_limit(self) -> int:
        return self.settings.RATE_LIMIT_IP_PER_MIN

    def check_and_consume(
        self,
        ip_address: str,
        tenant_id: str | None = None,
        now: float | None = None,
    ) -> tuple[bool, str, int]:
        """Check rate limits against global, tenant, and client IP sliding windows.

        Returns:
            (allowed: bool, reason: str, retry_after_seconds: int)
        """
        curr = now if now is not None else time.time()
        cutoff = curr - 60.0

        with self._lock:
            # 1. Prune timestamps older than 60s
            self._global_timestamps = [t for t in self._global_timestamps if t > cutoff]

            ip_list = [t for t in self._ip_timestamps[ip_address] if t > cutoff]
            self._ip_timestamps[ip_address] = ip_list

            if tenant_id:
                t_list = [t for t in self._tenant_timestamps[tenant_id] if t > cutoff]
                self._tenant_timestamps[tenant_id] = t_list
            else:
                t_list = []

            # 2. Check Global Limit
            if len(self._global_timestamps) >= self.global_limit:
                retry_after = max(1, int(self._global_timestamps[0] + 60.0 - curr))
                return (
                    False,
                    f"Global rate limit of {self.global_limit} requests/min exceeded.",
                    retry_after,
                )

            # 3. Check Tenant Limit
            if tenant_id and len(t_list) >= self.tenant_limit:
                retry_after = max(1, int(t_list[0] + 60.0 - curr))
                return (
                    False,
                    f"Tenant '{tenant_id}' rate limit of {self.tenant_limit} requests/min exceeded.",
                    retry_after,
                )

            # 4. Check Client IP Limit
            if len(ip_list) >= self.ip_limit:
                retry_after = max(1, int(ip_list[0] + 60.0 - curr))
                return (
                    False,
                    f"Client IP rate limit of {self.ip_limit} requests/min exceeded.",
                    retry_after,
                )

            # 5. Record request timestamp
            self._global_timestamps.append(curr)
            self._ip_timestamps[ip_address].append(curr)
            if tenant_id:
                self._tenant_timestamps[tenant_id].append(curr)

            return True, "", 0

    def reset(self) -> None:
        """Reset all rate limiter windows."""
        with self._lock:
            self._global_timestamps.clear()
            self._tenant_timestamps.clear()
            self._ip_timestamps.clear()


_rate_limiter_instance: SlidingWindowRateLimiter | None = None


def get_sliding_window_rate_limiter(
    settings: Settings | None = None,
) -> SlidingWindowRateLimiter:
    """Singleton getter for SlidingWindowRateLimiter."""
    global _rate_limiter_instance
    if _rate_limiter_instance is None:
        _rate_limiter_instance = SlidingWindowRateLimiter(settings)
    return _rate_limiter_instance


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforce global, per-tenant, and per-IP rate limits to prevent resource exhaustion."""

    def __init__(self, app: Any, limiter: SlidingWindowRateLimiter | None = None) -> None:
        super().__init__(app)
        self.limiter = limiter or get_sliding_window_rate_limiter()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # Exempt health checks, status page, and static assets from rate limiting
        if (
            path in ("/health", "/status", "/api/v1/status")
            or path.startswith("/static/")
            or request.method == "OPTIONS"
        ):
            return await call_next(request)

        # Extract client IP
        forwarded = request.headers.get("x-forwarded-for")
        client_ip = (
            forwarded.split(",")[0].strip()
            if forwarded
            else (request.client.host if request.client else "127.0.0.1")
        )

        tenant = tenant_id_ctx.get() or request.headers.get("x-tenant-id")

        allowed, reason, retry_after = self.limiter.check_and_consume(
            ip_address=client_ip,
            tenant_id=tenant,
        )

        if not allowed:
            logger.warning(
                f"Rate limit exceeded: {reason} (client={client_ip}, tenant={tenant})",
                extra={
                    "client_ip": client_ip,
                    "tenant_id": tenant,
                    "retry_after": retry_after,
                },
            )
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "detail": reason,
                    "error": {
                        "code": "TOO_MANY_REQUESTS",
                        "message": reason,
                        "status_code": status.HTTP_429_TOO_MANY_REQUESTS,
                        "details": {"retry_after": retry_after},
                    },
                },
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)
