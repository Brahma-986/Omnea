"""Database ORM models and schemas for procurement intents."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from procure_core.models.base import Base, TenantMixin, TimestampMixin


class ProcurementIntentModel(Base, TenantMixin, TimestampMixin):
    """Persisted procurement intake intent extracted from inbound communications."""

    __tablename__ = "procurement_intents"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("conversation_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("requests.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_event_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    intent_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="awaiting_clarification"
    )
    clarification_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Extracted entity fields
    vendor_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    replaces_vendor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    candidate_vendors: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    estimated_spend: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    annualized_spend: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    billing_period: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    department: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    # Verification evidence and missing fields
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    missing_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    clarifying_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ix_intent_tenant_status", "tenant_id", "status"),)


class ProcurementIntentSchema(BaseModel):
    """Pydantic representation of a persisted procurement intent."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: str
    session_id: uuid.UUID
    request_id: uuid.UUID | None = None
    source_event_id: str
    prompt_version: str
    model: str
    intent_type: str
    status: str
    clarification_count: int
    needs_review: bool
    vendor_name: str | None = None
    replaces_vendor: str | None = None
    candidate_vendors: list[str] = Field(default_factory=list)
    estimated_spend: Decimal | None = None
    annualized_spend: Decimal | None = None
    currency: str | None = None
    billing_period: str = "unknown"
    justification: str | None = None
    department: str | None = None
    tags: list[str] = Field(default_factory=list)
    evidence: dict[str, str] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    clarifying_question: str | None = None
    raw_text: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
