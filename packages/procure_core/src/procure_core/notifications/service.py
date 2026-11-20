"""Multichannel Notification Service honoring per-user channel preferences and event subscriptions."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from procure_core.db import get_async_session_factory
from procure_core.email.sender import EmailSenderProtocol, ResendEmailSender
from procure_core.models.notification import UserNotificationPreferenceModel
from procure_core.slack.client import ISlackClient

logger = logging.getLogger(__name__)


@dataclass
class NotificationDispatchResult:
    """Summary of multichannel notification delivery."""

    notification_id: str
    tenant_id: str
    user_id: str
    event_type: str
    channels_attempted: list[str] = field(default_factory=list)
    channels_delivered: list[str] = field(default_factory=list)
    skipped_by_preferences: list[str] = field(default_factory=list)


class NotificationService:
    """Delivers transactional alerts across In-App, Email, and Slack honoring per-user preferences."""

    def __init__(
        self,
        email_sender: EmailSenderProtocol | None = None,
        slack_client: ISlackClient | None = None,
        session_factory: Any = None,
    ) -> None:
        self.email_sender = email_sender or ResendEmailSender()
        self.slack_client = slack_client
        self.session_factory = session_factory or get_async_session_factory()

    async def get_user_preferences(
        self,
        session: AsyncSession,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """Fetch per-user notification preferences with safe defaults."""
        try:
            u_uuid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
            stmt = select(UserNotificationPreferenceModel).where(
                and_(
                    UserNotificationPreferenceModel.tenant_id == tenant_id,
                    UserNotificationPreferenceModel.user_id == u_uuid,
                )
            )
            res = await session.execute(stmt)
            pref = res.scalar_one_or_none()
            if pref:
                return {
                    "channels": pref.channels,
                    "event_subscriptions": pref.event_subscriptions,
                }
        except Exception:
            pass

        return {
            "channels": {"in_app": True, "email": True, "slack": True},
            "event_subscriptions": {
                "request_submitted": True,
                "approval_requested": True,
                "request_approved": True,
                "request_rejected": True,
                "drift_detected": True,
            },
        }

    async def notify_user(
        self,
        tenant_id: str,
        user_id: str,
        email: str,
        event_type: str,
        title: str,
        message: str,
        slack_channel: str | None = None,
        link: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> NotificationDispatchResult:
        """Evaluate preferences and deliver notification across authorized channels."""
        notif_id = f"notif-{uuid.uuid4().hex[:12]}"
        result = NotificationDispatchResult(
            notification_id=notif_id,
            tenant_id=tenant_id,
            user_id=user_id,
            event_type=event_type,
        )

        async with self.session_factory() as session:
            prefs = await self.get_user_preferences(session, tenant_id, user_id)

        channels_cfg: dict[str, bool] = prefs.get("channels", {})
        events_cfg: dict[str, bool] = prefs.get("event_subscriptions", {})

        # Check event subscription
        if not events_cfg.get(event_type, True):
            logger.info(
                "Notification %s skipped: User unsubscribed from event '%s'", notif_id, event_type
            )
            result.skipped_by_preferences.append("all_events")
            return result

        # 1. In-App Delivery
        if channels_cfg.get("in_app", True):
            result.channels_attempted.append("in_app")
            try:
                from api.routes.notifications import _in_memory_notifications

                key = (tenant_id, user_id)
                if key not in _in_memory_notifications:
                    _in_memory_notifications[key] = []
                _in_memory_notifications[key].insert(
                    0,
                    {
                        "id": notif_id,
                        "tenant_id": tenant_id,
                        "user_id": user_id,
                        "title": title,
                        "message": message,
                        "type": event_type,
                        "is_read": False,
                        "link": link or "/app",
                        "metadata": metadata or {},
                        "created_at": datetime.now(UTC).isoformat(),
                    },
                )
                result.channels_delivered.append("in_app")
            except Exception as in_app_err:
                logger.warning("In-app notification delivery degraded: %s", in_app_err)
        else:
            result.skipped_by_preferences.append("in_app")

        # 2. Email Delivery
        if channels_cfg.get("email", True) and email:
            result.channels_attempted.append("email")
            try:
                html_body = f"""
                <div style="font-family: sans-serif; max-width: 600px; padding: 20px;">
                  <h2 style="color: #2563eb;">{title}</h2>
                  <p>{message}</p>
                  {f'<p><a href="{link}">View Details in Omnea</a></p>' if link else ""}
                </div>
                """
                delivered = await self.email_sender.send_email(
                    to=email,
                    subject=f"[Omnea] {title}",
                    html_content=html_body,
                    text_content=message,
                )
                if delivered:
                    result.channels_delivered.append("email")
            except Exception as email_err:
                logger.warning("Email delivery failed: %s", email_err)
        else:
            result.skipped_by_preferences.append("email")

        # 3. Slack Delivery
        if channels_cfg.get("slack", True) and self.slack_client and slack_channel:
            result.channels_attempted.append("slack")
            try:
                slack_res = await self.slack_client.post_message(
                    channel=slack_channel,
                    text=f"*{title}*\n{message}",
                )
                if slack_res.ok:
                    result.channels_delivered.append("slack")
            except Exception as slack_err:
                logger.warning("Slack notification delivery failed: %s", slack_err)
        else:
            result.skipped_by_preferences.append("slack")

        logger.info(
            "Notification %s dispatched: attempted=%s, delivered=%s, skipped=%s",
            notif_id,
            result.channels_attempted,
            result.channels_delivered,
            result.skipped_by_preferences,
        )
        return result
