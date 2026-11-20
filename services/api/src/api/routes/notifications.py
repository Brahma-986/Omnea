"""In-app notifications management routes.

Provides endpoints for retrieving tenant and user scoped notifications with
mock initial seed fallback and marking notifications as read.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.permissions import AuthContext, require_permission
from api.routes.events import broadcast_tenant_event
from procure_core.db import get_async_session
from procure_core.log_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/notifications", tags=["In-App Notifications"])

# In-memory store for fallback/seeded notifications: (tenant_id, user_id) -> list[dict]
_in_memory_notifications: dict[tuple[str, str], list[dict[str, Any]]] = {}


class NotificationItem(BaseModel):
    """Notification item schema."""

    id: str
    tenant_id: str
    user_id: str
    title: str
    message: str
    type: str = "info"
    is_read: bool = False
    link: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class NotificationReadResponse(BaseModel):
    """Response after marking a notification as read."""

    status: str
    id: str
    is_read: bool


def _generate_seed_notifications(tenant_id: str, user_id: str) -> list[dict[str, Any]]:
    """Generate realistic initial procurement notifications for newly initialized users."""
    now = datetime.now(UTC)
    return [
        {
            "id": f"notif-{uuid.uuid4().hex[:12]}",
            "tenant_id": tenant_id,
            "user_id": user_id,
            "title": "PO #1042 approved by finance",
            "message": (
                "Purchase order #1042 for Snowflake Data Warehouse has been approved by "
                "Finance Admin."
            ),
            "type": "approval",
            "is_read": False,
            "link": "/app/requests/req-1042",
            "metadata": {"request_id": "req-1042", "category": "cloud_data"},
            "created_at": (now - timedelta(hours=1)).isoformat(),
        },
        {
            "id": f"notif-{uuid.uuid4().hex[:12]}",
            "tenant_id": tenant_id,
            "user_id": user_id,
            "title": "Vendor Datadog renewal requires security review",
            "message": (
                "Datadog annual subscription renewal requires SOC2 Type II "
                "compliance re-verification."
            ),
            "type": "warning",
            "is_read": False,
            "link": "/app/approvals/sec-883",
            "metadata": {"vendor_name": "Datadog, Inc.", "priority": "high"},
            "created_at": (now - timedelta(hours=3)).isoformat(),
        },
        {
            "id": f"notif-{uuid.uuid4().hex[:12]}",
            "tenant_id": tenant_id,
            "user_id": user_id,
            "title": "Spend cap warning for Cloud Infrastructure",
            "message": (
                "Cloud Infrastructure category has reached 85% ($85,000 / $100,000) of its monthly "
                "committed spend cap."
            ),
            "type": "action_required",
            "is_read": False,
            "link": "/app/finance/caps",
            "metadata": {"cap_limit": 100000, "current_spend": 85000},
            "created_at": (now - timedelta(days=1)).isoformat(),
        },
        {
            "id": f"notif-{uuid.uuid4().hex[:12]}",
            "tenant_id": tenant_id,
            "user_id": user_id,
            "title": "Welcome to Omnea Procurement Platform",
            "message": (
                "Your tenant workspace is configured and ready for AI-native autonomous "
                "procurement orchestration."
            ),
            "type": "info",
            "is_read": True,
            "link": "/app/dashboard",
            "metadata": {"platform_version": "2.0.0"},
            "created_at": (now - timedelta(days=2)).isoformat(),
        },
    ]


@router.get(
    "",
    response_model=list[NotificationItem],
    summary="Get user notifications",
)
@router.get(
    "/",
    response_model=list[NotificationItem],
    include_in_schema=False,
)
async def list_notifications(
    auth: AuthContext = Depends(require_permission("user:read")),
    session: AsyncSession = Depends(get_async_session),
) -> list[NotificationItem]:
    """Return in-app notifications for authenticated user's tenant & user_id.

    Queries the database table if present and populated, or falls back to
    realistic mock initial seed data.
    """
    key = (auth.tenant_id, auth.user_id)

    # 1. Attempt database query if notifications table exists
    try:
        query = text(
            """
            SELECT id, tenant_id, user_id, title, message, type, is_read, link, metadata, created_at
            FROM notifications
            WHERE tenant_id = :tenant_id AND user_id = :user_id
            ORDER BY created_at DESC;
            """
        )
        res = await session.execute(query, {"tenant_id": auth.tenant_id, "user_id": auth.user_id})
        rows = res.mappings().all()
        if rows:
            return [
                NotificationItem(
                    id=str(r["id"]),
                    tenant_id=str(r["tenant_id"]),
                    user_id=str(r["user_id"]),
                    title=str(r["title"]),
                    message=str(r["message"]),
                    type=str(r.get("type", "info")),
                    is_read=bool(r.get("is_read", False)),
                    link=r.get("link"),
                    metadata=dict(r.get("metadata") or {}),
                    created_at=(
                        r["created_at"].isoformat()
                        if hasattr(r["created_at"], "isoformat")
                        else str(r["created_at"])
                    ),
                )
                for r in rows
            ]
    except Exception:
        # Table does not exist or schema difference; gracefully fall through to seeded memory store
        pass

    # 2. Use in-memory store with initial seed if empty
    if key not in _in_memory_notifications:
        _in_memory_notifications[key] = _generate_seed_notifications(auth.tenant_id, auth.user_id)

    return [NotificationItem(**item) for item in _in_memory_notifications[key]]


@router.post(
    "/{notification_id}/read",
    response_model=NotificationReadResponse,
    summary="Mark notification as read",
)
async def mark_notification_read(
    notification_id: str,
    auth: AuthContext = Depends(require_permission("user:read")),
    session: AsyncSession = Depends(get_async_session),
) -> NotificationReadResponse:
    """Mark an in-app notification as read.

    Updates database row if present, updates in-memory seed store,
    and broadcasts a real-time event.
    """
    key = (auth.tenant_id, auth.user_id)
    updated = False

    # 1. Attempt DB update
    try:
        res = await session.execute(
            text(
                """
                UPDATE notifications
                SET is_read = TRUE, read_at = :now
                WHERE id = :id AND tenant_id = :tenant_id AND user_id = :user_id;
                """
            ),
            {
                "id": notification_id,
                "tenant_id": auth.tenant_id,
                "user_id": auth.user_id,
                "now": datetime.now(UTC),
            },
        )
        await session.commit()
        rc = getattr(res, "rowcount", 0)
        if rc and rc > 0:
            updated = True
    except Exception:
        pass

    # 2. Update in-memory notifications
    notifications = _in_memory_notifications.get(key, [])
    for item in notifications:
        if item["id"] == notification_id:
            item["is_read"] = True
            updated = True
            break

    if not updated and not notifications:
        # If notification store was never accessed, seed it and mark
        _in_memory_notifications[key] = _generate_seed_notifications(auth.tenant_id, auth.user_id)
        for item in _in_memory_notifications[key]:
            if item["id"] == notification_id:
                item["is_read"] = True
                updated = True
                break

    # Emit real-time notification update to tenant
    broadcast_tenant_event(
        auth.tenant_id,
        "notification_read",
        {"notification_id": notification_id, "user_id": auth.user_id},
    )

    return NotificationReadResponse(
        status="success",
        id=notification_id,
        is_read=True,
    )


class NotificationPreferencesRequest(BaseModel):
    """Payload to update multichannel notification preferences."""

    channels: dict[str, bool] = Field(default_factory=dict)
    event_subscriptions: dict[str, bool] = Field(default_factory=dict)


@router.get("/preferences")
async def get_notification_preferences(
    auth: AuthContext = Depends(require_permission("user:read")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Retrieve current user's multichannel notification preferences."""
    from procure_core.notifications.service import NotificationService

    service = NotificationService(session_factory=lambda: db)
    prefs = await service.get_user_preferences(db, auth.tenant_id, auth.user_id)
    return prefs


@router.put("/preferences")
async def update_notification_preferences(
    body: NotificationPreferencesRequest,
    auth: AuthContext = Depends(require_permission("user:read")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Update current user's multichannel notification preferences."""
    from sqlalchemy import and_, select

    from procure_core.models.notification import UserNotificationPreferenceModel

    u_uuid = uuid.UUID(auth.user_id) if isinstance(auth.user_id, str) else auth.user_id
    stmt = select(UserNotificationPreferenceModel).where(
        and_(
            UserNotificationPreferenceModel.tenant_id == auth.tenant_id,
            UserNotificationPreferenceModel.user_id == u_uuid,
        )
    )
    res = await db.execute(stmt)
    existing = res.scalar_one_or_none()

    default_channels = {"in_app": True, "email": True, "slack": True}
    default_events = {
        "request_submitted": True,
        "approval_requested": True,
        "request_approved": True,
        "request_rejected": True,
        "drift_detected": True,
    }

    if existing:
        if body.channels:
            merged_ch = {**existing.channels, **body.channels}
            existing.channels = merged_ch
        if body.event_subscriptions:
            merged_ev = {**existing.event_subscriptions, **body.event_subscriptions}
            existing.event_subscriptions = merged_ev
        pref = existing
    else:
        pref = UserNotificationPreferenceModel(
            tenant_id=auth.tenant_id,
            user_id=u_uuid,
            channels={**default_channels, **body.channels},
            event_subscriptions={**default_events, **body.event_subscriptions},
        )
        db.add(pref)

    await db.commit()
    await db.refresh(pref)
    return {
        "channels": pref.channels,
        "event_subscriptions": pref.event_subscriptions,
    }
