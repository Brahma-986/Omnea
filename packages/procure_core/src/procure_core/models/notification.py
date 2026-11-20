"""SQLAlchemy ORM models for per-user notification preferences."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from procure_core.models.base import Base


class UserNotificationPreferenceModel(Base):
    """Per-user multichannel delivery preferences and event subscriptions."""

    __tablename__ = "user_notification_preferences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    channels: Mapped[dict[str, bool]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {"in_app": True, "email": True, "slack": True},
    )
    event_subscriptions: Mapped[dict[str, bool]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {
            "request_submitted": True,
            "approval_requested": True,
            "request_approved": True,
            "request_rejected": True,
            "drift_detected": True,
        },
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_user_notif_pref"),
        Index("idx_user_notif_pref_lookup", "tenant_id", "user_id"),
    )
