"""Data models and schemas for ERP connectors and sync payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any


@dataclass
class ErpConnectionTestResult:
    """Result of ERP connector connectivity and authentication test."""

    connected: bool
    connector_type: str
    message: str
    latency_ms: float = 0.0
    authenticated_identity: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ErpVendorPayload:
    """Vendor synchronization payload for ERP."""

    vendor_name: str
    external_id: str
    currency: str = "USD"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ErpVendorResult:
    """Result of vendor synchronization in ERP."""

    vendor_id: str
    external_id: str
    name: str
    status: str
    is_new: bool = False


@dataclass
class ErpPurchaseOrderPayload:
    """Purchase Order creation payload for ERP."""

    po_number: str
    request_id: str
    vendor_id: str
    vendor_name: str
    amount: Decimal
    currency: str
    department: str
    idempotency_key: str
    line_items: list[dict[str, Any]] = field(default_factory=list)
    description: str = "Procurement Order"


@dataclass
class ErpPurchaseOrderResult:
    """Purchase Order response from ERP."""

    po_number: str
    request_id: str
    vendor_id: str
    vendor_name: str
    amount: Decimal
    currency: str
    status: str  # draft, open, fulfilled, cancelled
    erp_po_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    line_items: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ErpCommitmentPayload:
    """Budget commitment recording payload for ERP."""

    po_number: str
    department: str
    amount: Decimal
    currency: str
    period: str
    idempotency_key: str


@dataclass
class ErpCommitmentResult:
    """Budget commitment recording result in ERP."""

    commitment_id: str
    po_number: str
    department: str
    amount: Decimal
    currency: str
    period: str
    status: str = "committed"
