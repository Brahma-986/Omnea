"""Domain models, enums, and SQLAlchemy ORM models for the ingress engine."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from procure_core.models.base import Base, TenantMixin, TimestampMixin
from procure_core.storage.base import AttachmentReference


class ChannelType(StrEnum):
    """Supported communication channels."""

    SLACK = "slack"
    TEAMS = "teams"
    EMAIL = "email"


class QueueStatus(StrEnum):
    """Queue processing states."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class InboundMessage(BaseModel):
    """Normalized internal canonical model for all inbound communications."""

    model_config = ConfigDict(from_attributes=True)

    channel: ChannelType
    user_id: str
    thread_id: str
    conversation_id: str
    text: str
    attachments: list[AttachmentReference] = Field(default_factory=list)
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tenant_id: str = "tenant_default"
    ordering_key: str = ""
    response_url: str | None = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class InboundEventQueueModel(Base, TenantMixin, TimestampMixin):
    """Postgres-backed transactional inbound event queue with dedup and locking."""

    __tablename__ = "inbound_event_queue"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    ordering_key: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    normalized_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=QueueStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lock_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("uq_queue_channel_event_id", "channel", "event_id", unique=True),
        Index("ix_queue_ordering_key", "ordering_key"),
    )


class ConversationSessionModel(Base, TenantMixin, TimestampMixin):
    """Active conversational context keyed per thread with TTL."""

    __tablename__ = "conversation_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("requests.id", ondelete="SET NULL"),
        nullable=True,
    )
    session_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "uq_session_channel_thread",
            "tenant_id",
            "channel_type",
            "conversation_id",
            "thread_id",
            unique=True,
        ),
    )


class QueueItem(BaseModel):
    """Pydantic representation of an enqueued event."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: str
    ordering_key: str
    channel: str
    event_id: str
    payload: dict[str, Any]
    normalized_payload: dict[str, Any] | None = None
    status: str
    attempts: int
    max_attempts: int
    locked_at: datetime | None = None
    lock_expires_at: datetime | None = None
    available_at: datetime
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class SessionItem(BaseModel):
    """Pydantic representation of a conversation session."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: str
    channel_type: str
    conversation_id: str
    thread_id: str
    request_id: uuid.UUID | None = None
    session_data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
