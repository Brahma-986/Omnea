"""SQLAlchemy ORM models for ERP systems of record, ledger, saga, and reconciliation."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from procure_core.models.base import Base


class ErpConfigurationModel(Base):
    """Tenant-scoped ERP connector configuration and secret references."""

    __tablename__ = "erp_configurations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(32), nullable=False, default="ledger")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_connected")
    secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "connector_type", name="uq_erp_config_tenant"),
        Index("idx_erp_config_tenant_status", "tenant_id", "status"),
    )


class ErpLedgerVendorModel(Base):
    """Built-in ledger ERP vendor master record."""

    __tablename__ = "erp_ledger_vendors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    erp_vendor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "erp_vendor_id", name="uq_erp_ledger_vendor"),
        Index("idx_erp_ledger_vendors_tenant", "tenant_id"),
    )


class ErpLedgerPurchaseOrderModel(Base):
    """Built-in ledger ERP Purchase Order master record."""

    __tablename__ = "erp_ledger_purchase_orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    po_number: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    vendor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    line_items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    commitments: Mapped[list[ErpLedgerCommitmentModel]] = relationship(
        "ErpLedgerCommitmentModel", back_populates="purchase_order", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "po_number", name="uq_erp_ledger_po_number"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_erp_ledger_po_idempotency"),
        Index("idx_erp_ledger_po_tenant", "tenant_id", "request_id"),
    )


class ErpLedgerCommitmentModel(Base):
    """Built-in ledger ERP commitments tied to purchase orders."""

    __tablename__ = "erp_ledger_commitments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    po_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("erp_ledger_purchase_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    po_number: Mapped[str] = mapped_column(String(64), nullable=False)
    department: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    period: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="committed")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    purchase_order: Mapped[ErpLedgerPurchaseOrderModel] = relationship(
        "ErpLedgerPurchaseOrderModel", back_populates="commitments"
    )

    __table_args__ = (Index("idx_erp_ledger_commitments_tenant", "tenant_id", "po_number"),)


class ErpSagaExecutionModel(Base):
    """Tracks saga lifecycle state, current step, and compensation trail."""

    __tablename__ = "erp_saga_executions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    saga_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="started")
    current_step: Mapped[str] = mapped_column(String(64), nullable=False, default="init")
    compensation_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    po_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_erp_saga_idempotency"),
        Index("idx_erp_saga_tenant_req", "tenant_id", "request_id"),
    )


class ErpReconciliationRunModel(Base):
    """Record of periodic or on-demand ERP reconciliation runs."""

    __tablename__ = "erp_reconciliation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    total_checked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    drift_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    findings: Mapped[list[ErpReconciliationFindingModel]] = relationship(
        "ErpReconciliationFindingModel", back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("idx_erp_rec_runs_tenant", "tenant_id"),)


class ErpReconciliationFindingModel(Base):
    """Specific drift finding discovered between Omnea state and ERP state."""

    __tablename__ = "erp_reconciliation_findings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("erp_reconciliation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    po_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finding_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default="medium")
    omnea_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    erp_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolution_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run: Mapped[ErpReconciliationRunModel] = relationship(
        "ErpReconciliationRunModel", back_populates="findings"
    )
    repairs: Mapped[list[ErpReconciliationRepairModel]] = relationship(
        "ErpReconciliationRepairModel", back_populates="finding", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("idx_erp_rec_findings_tenant", "tenant_id", "is_resolved"),)


class ErpReconciliationRepairModel(Base):
    """Audited repair actions executed against reconciliation drift findings."""

    __tablename__ = "erp_reconciliation_repairs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("erp_reconciliation_findings.id", ondelete="CASCADE"),
        nullable=False,
    )
    repair_action: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    audit_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    finding: Mapped[ErpReconciliationFindingModel] = relationship(
        "ErpReconciliationFindingModel", back_populates="repairs"
    )

    __table_args__ = (Index("idx_erp_repairs_tenant", "tenant_id", "finding_id"),)
