"""Procurement requests and approval ORM models and schemas."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from procure_core.models.base import Base, TenantMixin, TimestampMixin


class RequestModel(Base, TenantMixin, TimestampMixin):
    """Procurement request domain model."""

    __tablename__ = "requests"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    requester_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    approvals: Mapped[list["ApprovalModel"]] = relationship(
        "ApprovalModel",
        back_populates="request",
        cascade="all, delete-orphan",
    )

    @property
    def estimated_spend(self) -> Decimal | None:
        from decimal import Decimal

        val = (self.payload or {}).get("estimated_spend")
        if val is None:
            val = (self.payload or {}).get("amount")
        if val is not None:
            return Decimal(str(val))
        return None

    @property
    def currency(self) -> str:
        return (self.payload or {}).get("currency", "USD")

    @property
    def vendor_name(self) -> str:
        return (self.payload or {}).get("vendor_name", "Vendor")

    @property
    def department(self) -> str:
        return (self.payload or {}).get("department", "Engineering")


class ApprovalModel(Base, TenantMixin, TimestampMixin):
    """Approval lifecycle step for a procurement request."""

    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approver_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    decision_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    request: Mapped["RequestModel"] = relationship("RequestModel", back_populates="approvals")


class RequestSchema(BaseModel):
    """Pydantic representation of a procurement request."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: str
    requester_id: str
    status: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ApprovalSchema(BaseModel):
    """Pydantic representation of an approval step."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: str
    request_id: uuid.UUID
    step: int
    approver_id: str
    status: str
    decision_payload: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
