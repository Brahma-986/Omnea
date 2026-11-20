"""Server-Sent Events (SSE) real-time streaming updates router.

Provides tenant-scoped SSE event streams with periodic keep-alive pings,
short-lived single-use stream ticket authentication, and broadcast helpers.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections import defaultdict
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from api.permissions import AuthContext, require_permission
from procure_core.db import get_async_session_factory
from procure_core.log_config import get_logger, tenant_id_ctx

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/events", tags=["Real-Time Events"])

# Tenant-scoped SSE subscriber queues: tenant_id -> set of asyncio.Queue
_tenant_subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

# Short-lived single-use stream tickets: ticket -> (user_id, tenant_id, roles, expires_at)
_stream_tickets: dict[str, tuple[str, str, list[str], float]] = {}


def _clean_expired_tickets() -> None:
    """Purge expired tickets from memory."""
    now = time.time()
    expired = [t for t, v in _stream_tickets.items() if v[3] < now]
    for t in expired:
        _stream_tickets.pop(t, None)


def _add_subscriber(tenant_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
    """Register an active event queue for a tenant."""
    _tenant_subscribers[tenant_id].add(queue)


def _remove_subscriber(tenant_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
    """Unregister an event queue for a tenant on disconnect."""
    if tenant_id in _tenant_subscribers:
        _tenant_subscribers[tenant_id].discard(queue)
        if not _tenant_subscribers[tenant_id]:
            del _tenant_subscribers[tenant_id]


class _BroadcastResult(int):
    """Awaitable integer result enabling both sync and async invocations."""

    def __await__(self) -> Any:
        async def _resolve() -> int:
            return int(self)

        return _resolve().__await__()


def broadcast_tenant_event(
    tenant_id: str,
    event_type: str,
    data: dict[str, Any],
) -> _BroadcastResult:
    """Broadcast a real-time event to all active SSE client queues scoped to tenant_id.

    Can be invoked synchronously or asynchronously:
        count = broadcast_tenant_event("t-123", "approval_needed", {...})
        count = await broadcast_tenant_event("t-123", "approval_needed", {...})

    Args:
        tenant_id: Target tenant identifier.
        event_type: SSE event name (e.g., 'request_created', 'approval_updated').
        data: JSON-serializable event payload dictionary.

    Returns:
        Number of active client queues that received the event.
    """
    subscribers = _tenant_subscribers.get(tenant_id)
    if not subscribers:
        return _BroadcastResult(0)

    delivered = 0
    payload = {"event": event_type, "data": data}
    dead_queues: set[asyncio.Queue[dict[str, Any]]] = set()

    for queue in list(subscribers):
        try:
            queue.put_nowait(payload)
            delivered += 1
        except Exception:
            dead_queues.add(queue)

    for dead in dead_queues:
        subscribers.discard(dead)

    return _BroadcastResult(delivered)


async def sse_event_generator(
    request: Request,
    tenant_id: str,
    user_id: str,
) -> AsyncGenerator[str, None]:
    """Yield Server-Sent Events with keep-alive pings every 15 seconds."""
    tenant_id_ctx.set(tenant_id)
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
    _add_subscriber(tenant_id, queue)
    logger.info(
        f"SSE connection opened for tenant '{tenant_id}' user '{user_id}'",
        extra={"tenant_id": tenant_id, "user_id": user_id},
    )

    try:
        # Initial connection acknowledgment event
        init_payload = json.dumps(
            {
                "status": "connected",
                "tenant_id": tenant_id,
                "user_id": user_id,
            }
        )
        yield f"event: connected\ndata: {init_payload}\n\n"

        from procure_core.config import get_settings

        ping_interval = 1.0 if get_settings().APP_ENV in ("test", "development") else 15.0
        disconnect_task = asyncio.create_task(request.is_disconnected())
        try:
            while True:
                get_task = asyncio.create_task(queue.get())
                done, pending = await asyncio.wait(
                    [disconnect_task, get_task],
                    timeout=ping_interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if disconnect_task in done:
                    get_task.cancel()
                    break

                if get_task in done:
                    item = get_task.result()
                    event_type = str(item.get("event", "message"))
                    data_json = json.dumps(item.get("data", {}))
                    yield f"event: {event_type}\ndata: {data_json}\n\n"
                else:
                    # Timeout reached without event: emit keep-alive ping
                    get_task.cancel()
                    yield ": ping\n\n"
        finally:
            disconnect_task.cancel()
    except asyncio.CancelledError:
        pass
    finally:
        _remove_subscriber(tenant_id, queue)
        tenant_id_ctx.set(tenant_id)
        logger.info(
            f"SSE connection closed for tenant '{tenant_id}' user '{user_id}'",
            extra={"tenant_id": tenant_id, "user_id": user_id},
        )


class StreamTicketResponse(BaseModel):
    ticket: str
    expires_in: int = 30


@router.post(
    "/ticket",
    response_model=StreamTicketResponse,
    summary="Issue a short-lived single-use SSE stream ticket",
)
async def issue_stream_ticket(
    auth: AuthContext = Depends(require_permission("user:read")),
) -> StreamTicketResponse:
    """Generate a cryptographically secure, short-lived single-use stream ticket for SSE."""
    _clean_expired_tickets()
    ticket = secrets.token_urlsafe(32)
    expires_at = time.time() + 30.0
    _stream_tickets[ticket] = (auth.user_id, auth.tenant_id, list(auth.roles), expires_at)
    return StreamTicketResponse(ticket=ticket, expires_in=30)


@router.get("/stream", summary="Real-time Server-Sent Events stream")
async def stream_events(
    request: Request,
    ticket: str | None = Query(default=None, description="Short-lived single-use stream ticket"),
    token: str | None = Query(default=None, description="Forbidden raw JWT query parameter"),
) -> StreamingResponse:
    """Stream real-time tenant events via Server-Sent Events (SSE).

    Authentication is supported via:
    1. Short-lived single-use stream ticket (?ticket=...)
    2. Same-origin httpOnly cookie (access_token)
    3. Standard Authorization: Bearer header
    Passing raw JWTs via ?token= is forbidden for security.
    """
    # 1. Reject forbidden raw token in URL query string
    if token and token.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Passing authentication tokens in URL query parameter '?token=' is forbidden for "
                "security. Use httpOnly cookies or single-use stream tickets via POST /api/v1/events/ticket."
            ),
        )

    # 2. Check ticket if provided
    if ticket and ticket.strip():
        _clean_expired_tickets()
        ticket_data = _stream_tickets.pop(ticket.strip(), None)
        if not ticket_data:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid, expired, or already used stream ticket",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user_id, tenant_id, roles, expires_at = ticket_data
        if time.time() > expires_at:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Stream ticket has expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        tenant_id_ctx.set(tenant_id)
        auth = AuthContext(
            user_id=user_id,
            tenant_id=tenant_id,
            roles=roles,
            permissions={"user:read"},
        )
    else:
        # Authenticate via cookie or Authorization header
        dep = require_permission("user:read")
        factory = get_async_session_factory()
        async with factory() as session:
            auth = await dep(request=request, session=session)

    # Ensure verified tenant is bound in context for all SSE logs
    tenant_id_ctx.set(auth.tenant_id)
    return StreamingResponse(
        sse_event_generator(request, auth.tenant_id, auth.user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
