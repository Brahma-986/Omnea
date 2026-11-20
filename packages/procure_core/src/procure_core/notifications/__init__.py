"""Multichannel Notification Service Package."""

from procure_core.notifications.service import (
    NotificationDispatchResult,
    NotificationService,
)

__all__ = [
    "NotificationService",
    "NotificationDispatchResult",
]
