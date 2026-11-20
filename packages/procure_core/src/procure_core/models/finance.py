"""SQLAlchemy ORM models for finance: budgets, reservations, and immutable policies."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from procure_core.models.base import Base


class ApprovalPolicyModel(Base):
    """Immutable per version + content_hash approval policies."""

    __tablename__ = "approval_policies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1.0")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "version", name="uq_approval_policy_version"),
        Index("ix_approval_policies_tenant", "tenant_id", "is_active"),
    )


class DepartmentBudgetModel(Base):
    """Department allocated budgets per currency and fiscal period."""

    __tablename__ = "department_budgets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    department: Mapped[str] = mapped_column(String(128), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    period: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    committed: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0.0000")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "department", "currency", "period", name="uq_dept_budget_period"
        ),
        CheckConstraint("amount >= 0", name="chk_dept_budget_positive"),
        CheckConstraint("committed >= 0", name="chk_dept_budget_committed"),
        CheckConstraint("committed <= amount", name="chk_dept_budget_committed_le_amount"),
        Index("ix_dept_budgets_lookup", "tenant_id", "department", "currency"),
    )


class BudgetReservationModel(Base):
    """Active and committed budget reservations against department budgets."""

    __tablename__ = "budget_reservations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    department: Mapped[str] = mapped_column(String(128), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    period: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_budget_reservation_idempotency"),
        ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            ["requests.tenant_id", "requests.id"],
            name="fk_budget_res_request",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('active', 'committed', 'released')", name="chk_budget_res_status"
        ),
        CheckConstraint("amount >= 0", name="chk_budget_res_amount"),
        Index(
            "ix_budget_reservations_lookup",
            "tenant_id",
            "department",
            "currency",
            "period",
            "status",
        ),
    )
