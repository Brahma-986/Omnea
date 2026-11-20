"""Built-in Ledger ERP Connector backed by PostgreSQL tables.

Guarantees:
1. Always works out of the box with zero external configuration.
2. Implements exact same ErpConnector interface as NetSuite, SAP, and Workday.
3. Implements lookup-before-retry on PO creation and commitments.
4. Counts POs and spend strictly from its own tables.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from sqlalchemy import and_, select, update

from procure_core.db import get_async_session_factory
from procure_core.erp.base import ErpConnector
from procure_core.erp.models import (
    ErpCommitmentPayload,
    ErpCommitmentResult,
    ErpConnectionTestResult,
    ErpPurchaseOrderPayload,
    ErpPurchaseOrderResult,
    ErpVendorPayload,
    ErpVendorResult,
)
from procure_core.models.erp import (
    ErpLedgerCommitmentModel,
    ErpLedgerPurchaseOrderModel,
    ErpLedgerVendorModel,
)

logger = logging.getLogger(__name__)


class LedgerErpConnector(ErpConnector):
    """Production-grade built-in ledger ERP connector backed by PostgreSQL tables."""

    def __init__(self, tenant_id: str, session_factory: Any = None) -> None:
        super().__init__(tenant_id=tenant_id, connector_type="ledger")
        self.session_factory = session_factory or get_async_session_factory()

    async def test_connection(self) -> ErpConnectionTestResult:
        """Test database connectivity to the built-in ledger tables."""
        start = time.perf_counter()
        try:
            async with self.session_factory() as session:
                await session.execute(
                    select(ErpLedgerVendorModel.id)
                    .where(ErpLedgerVendorModel.tenant_id == self.tenant_id)
                    .limit(1)
                )
            latency = round((time.perf_counter() - start) * 1000, 2)
            await self.circuit_breaker.record_success()
            return ErpConnectionTestResult(
                connected=True,
                connector_type="ledger",
                message="Built-in ledger ERP connected and operational.",
                latency_ms=latency,
                authenticated_identity=f"tenant://{self.tenant_id}/ledger",
                details={"engine": "postgresql", "status": "active"},
            )
        except Exception as exc:
            await self.circuit_breaker.record_failure(exc)
            latency = round((time.perf_counter() - start) * 1000, 2)
            return ErpConnectionTestResult(
                connected=False,
                connector_type="ledger",
                message=f"Ledger ERP database connection failed: {exc}",
                latency_ms=latency,
            )

    async def sync_vendor(self, vendor: ErpVendorPayload) -> ErpVendorResult:
        """Look up or create vendor record in ledger tables."""
        async with self.session_factory() as session:
            async with session.begin():
                stmt = select(ErpLedgerVendorModel).where(
                    and_(
                        ErpLedgerVendorModel.tenant_id == self.tenant_id,
                        ErpLedgerVendorModel.erp_vendor_id == vendor.external_id,
                    )
                )
                res = await session.execute(stmt)
                existing = res.scalar_one_or_none()

                if existing:
                    return ErpVendorResult(
                        vendor_id=str(existing.id),
                        external_id=existing.erp_vendor_id,
                        name=existing.name,
                        status=existing.status,
                        is_new=False,
                    )

                new_vendor = ErpLedgerVendorModel(
                    tenant_id=self.tenant_id,
                    erp_vendor_id=vendor.external_id,
                    name=vendor.vendor_name,
                    currency=vendor.currency,
                    status="active",
                )
                session.add(new_vendor)
                await session.flush()
                return ErpVendorResult(
                    vendor_id=str(new_vendor.id),
                    external_id=new_vendor.erp_vendor_id,
                    name=new_vendor.name,
                    status=new_vendor.status,
                    is_new=True,
                )

    async def create_purchase_order(
        self,
        po_payload: ErpPurchaseOrderPayload,
    ) -> ErpPurchaseOrderResult:
        """Lookup-before-retry PO creation: idempotent against duplicate retries."""
        async with self.session_factory() as session:
            async with session.begin():
                # 1. Lookup-before-create: check if PO already exists by po_number or idempotency_key
                stmt = select(ErpLedgerPurchaseOrderModel).where(
                    and_(
                        ErpLedgerPurchaseOrderModel.tenant_id == self.tenant_id,
                        (
                            (ErpLedgerPurchaseOrderModel.po_number == po_payload.po_number)
                            | (
                                ErpLedgerPurchaseOrderModel.idempotency_key
                                == po_payload.idempotency_key
                            )
                        ),
                    )
                )
                res = await session.execute(stmt)
                existing = res.scalar_one_or_none()

                if existing:
                    logger.info(
                        "Lookup-before-retry hit: PO '%s' already exists in ledger ERP.",
                        existing.po_number,
                    )
                    return ErpPurchaseOrderResult(
                        po_number=existing.po_number,
                        request_id=str(existing.request_id),
                        vendor_id=existing.vendor_id,
                        vendor_name=existing.vendor_name,
                        amount=existing.amount,
                        currency=existing.currency,
                        status=existing.status,
                        erp_po_id=str(existing.id),
                        created_at=existing.created_at,
                        line_items=existing.line_items,
                    )

                # 2. Insert new Purchase Order
                req_uuid = uuid.UUID(po_payload.request_id)
                new_po = ErpLedgerPurchaseOrderModel(
                    tenant_id=self.tenant_id,
                    po_number=po_payload.po_number,
                    request_id=req_uuid,
                    vendor_id=po_payload.vendor_id,
                    vendor_name=po_payload.vendor_name,
                    amount=po_payload.amount,
                    currency=po_payload.currency,
                    status="open",
                    line_items=po_payload.line_items,
                    idempotency_key=po_payload.idempotency_key,
                )
                session.add(new_po)
                await session.flush()

                return ErpPurchaseOrderResult(
                    po_number=new_po.po_number,
                    request_id=str(new_po.request_id),
                    vendor_id=new_po.vendor_id,
                    vendor_name=new_po.vendor_name,
                    amount=new_po.amount,
                    currency=new_po.currency,
                    status=new_po.status,
                    erp_po_id=str(new_po.id),
                    created_at=new_po.created_at,
                    line_items=new_po.line_items,
                )

    async def get_purchase_order(self, po_number: str) -> ErpPurchaseOrderResult | None:
        """Query ledger ERP for purchase order by PO number."""
        async with self.session_factory() as session:
            stmt = select(ErpLedgerPurchaseOrderModel).where(
                and_(
                    ErpLedgerPurchaseOrderModel.tenant_id == self.tenant_id,
                    ErpLedgerPurchaseOrderModel.po_number == po_number,
                )
            )
            res = await session.execute(stmt)
            po = res.scalar_one_or_none()
            if not po:
                return None
            return ErpPurchaseOrderResult(
                po_number=po.po_number,
                request_id=str(po.request_id),
                vendor_id=po.vendor_id,
                vendor_name=po.vendor_name,
                amount=po.amount,
                currency=po.currency,
                status=po.status,
                erp_po_id=str(po.id),
                created_at=po.created_at,
                line_items=po.line_items,
            )

    async def record_commitment(
        self,
        commitment: ErpCommitmentPayload,
    ) -> ErpCommitmentResult:
        """Lookup-before-retry commitment record linked to Purchase Order."""
        async with self.session_factory() as session:
            async with session.begin():
                po_stmt = select(ErpLedgerPurchaseOrderModel).where(
                    and_(
                        ErpLedgerPurchaseOrderModel.tenant_id == self.tenant_id,
                        ErpLedgerPurchaseOrderModel.po_number == commitment.po_number,
                    )
                )
                res = await session.execute(po_stmt)
                po = res.scalar_one_or_none()
                if not po:
                    raise ValueError(
                        f"Cannot record commitment: PO '{commitment.po_number}' not found"
                    )

                # Lookup existing commitment for this PO
                c_stmt = select(ErpLedgerCommitmentModel).where(
                    and_(
                        ErpLedgerCommitmentModel.tenant_id == self.tenant_id,
                        ErpLedgerCommitmentModel.po_id == po.id,
                    )
                )
                c_res = await session.execute(c_stmt)
                existing_c = c_res.scalar_one_or_none()
                if existing_c:
                    return ErpCommitmentResult(
                        commitment_id=str(existing_c.id),
                        po_number=existing_c.po_number,
                        department=existing_c.department,
                        amount=existing_c.amount,
                        currency=existing_c.currency,
                        period=existing_c.period,
                        status=existing_c.status,
                    )

                new_c = ErpLedgerCommitmentModel(
                    tenant_id=self.tenant_id,
                    po_id=po.id,
                    po_number=po.po_number,
                    department=commitment.department,
                    amount=commitment.amount,
                    currency=commitment.currency,
                    period=commitment.period,
                    status="committed",
                )
                session.add(new_c)
                await session.flush()

                return ErpCommitmentResult(
                    commitment_id=str(new_c.id),
                    po_number=new_c.po_number,
                    department=new_c.department,
                    amount=new_c.amount,
                    currency=new_c.currency,
                    period=new_c.period,
                    status=new_c.status,
                )

    async def list_purchase_orders(self, limit: int = 100) -> list[ErpPurchaseOrderResult]:
        """List all Purchase Orders directly from the ledger ERP table."""
        async with self.session_factory() as session:
            stmt = (
                select(ErpLedgerPurchaseOrderModel)
                .where(ErpLedgerPurchaseOrderModel.tenant_id == self.tenant_id)
                .order_by(ErpLedgerPurchaseOrderModel.created_at.desc())
                .limit(limit)
            )
            res = await session.execute(stmt)
            rows = res.scalars().all()
            return [
                ErpPurchaseOrderResult(
                    po_number=r.po_number,
                    request_id=str(r.request_id),
                    vendor_id=r.vendor_id,
                    vendor_name=r.vendor_name,
                    amount=r.amount,
                    currency=r.currency,
                    status=r.status,
                    erp_po_id=str(r.id),
                    created_at=r.created_at,
                    line_items=r.line_items,
                )
                for r in rows
            ]

    async def cancel_purchase_order(self, po_number: str, reason: str = "Order cancelled") -> bool:
        """Cancel/void a Purchase Order and release commitment in ledger ERP."""
        _ = reason
        async with self.session_factory() as session:
            async with session.begin():
                stmt = (
                    update(ErpLedgerPurchaseOrderModel)
                    .where(
                        and_(
                            ErpLedgerPurchaseOrderModel.tenant_id == self.tenant_id,
                            ErpLedgerPurchaseOrderModel.po_number == po_number,
                        )
                    )
                    .values(status="cancelled")
                )
                res = await session.execute(stmt)
                if getattr(res, "rowcount", 0) == 0:
                    return False

                # Void associated commitments
                c_stmt = (
                    update(ErpLedgerCommitmentModel)
                    .where(
                        and_(
                            ErpLedgerCommitmentModel.tenant_id == self.tenant_id,
                            ErpLedgerCommitmentModel.po_number == po_number,
                        )
                    )
                    .values(status="voided")
                )
                await session.execute(c_stmt)
                return True
