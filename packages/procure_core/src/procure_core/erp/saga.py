"""Idempotent ERP Saga Engine with Lookup-Before-Retry, Compensation, and Crash Hooks."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from procure_core.erp.base import CircuitBreakerOpenException, ErpConnector
from procure_core.erp.models import (
    ErpCommitmentPayload,
    ErpPurchaseOrderPayload,
    ErpVendorPayload,
)
from procure_core.models.erp import ErpSagaExecutionModel
from procure_core.models.request import RequestModel

logger = logging.getLogger(__name__)


class CrashSimulationException(Exception):
    """Exception injected to simulate process crash or worker kill."""

    def __init__(self, point_name: str) -> None:
        super().__init__(f"Crash simulated at point: {point_name}")
        self.point_name = point_name


@dataclass
class ErpSagaResult:
    """Outcome of ERP sync saga execution."""

    success: bool
    saga_id: str
    po_number: str | None
    status: str  # completed, compensated, failed, recovered
    steps_executed: list[str]
    error: str | None = None


class ErpSyncSaga:
    """Orchestrates multi-step distributed ERP synchronization with compensation and crash recovery."""

    def __init__(self, connector: ErpConnector, tenant_id: str) -> None:
        self.connector = connector
        self.tenant_id = tenant_id

    async def execute(
        self,
        session: AsyncSession,
        request_id: str,
        vendor_name: str,
        amount: Decimal,
        currency: str,
        department: str = "Engineering",
        line_items: list[dict[str, Any]] | None = None,
        fault_injection_point: str | None = None,
    ) -> ErpSagaResult:
        """Execute the 4-phase saga with lookup-before-retry, compensation, and crash points."""
        steps_executed: list[str] = []
        idem_key = f"{self.tenant_id}:{request_id}:erp_saga"
        saga_id = f"saga-{uuid.uuid4().hex[:12]}"
        req_uuid = uuid.UUID(request_id) if isinstance(request_id, str) else request_id
        po_number = f"PO-{self.tenant_id[:4].upper()}-{str(req_uuid)[:8].upper()}"

        # 0. Circuit Breaker Check
        if not await self.connector.circuit_breaker.can_execute():
            raise CircuitBreakerOpenException(
                f"ERP Circuit Breaker is OPEN for tenant '{self.tenant_id}'. Call aborted.",
                consecutive_failures=self.connector.circuit_breaker.failure_count,
            )

        # 1. Rate Limiter Acquisition
        acquired = await self.connector.rate_limiter.acquire(1.0)
        if not acquired:
            await self.connector.rate_limiter.wait_and_acquire(1.0, timeout=5.0)

        # 2. Lookup existing saga record
        stmt = select(ErpSagaExecutionModel).where(
            and_(
                ErpSagaExecutionModel.tenant_id == self.tenant_id,
                ErpSagaExecutionModel.idempotency_key == idem_key,
            )
        )
        res = await session.execute(stmt)
        saga_rec = res.scalar_one_or_none()

        if saga_rec and saga_rec.status == "completed":
            logger.info(
                "Saga already completed for request %s (PO: %s)", request_id, saga_rec.po_number
            )
            return ErpSagaResult(
                success=True,
                saga_id=saga_rec.saga_id,
                po_number=saga_rec.po_number,
                status="completed",
                steps_executed=["idempotency_cache_hit"],
            )

        if not saga_rec:
            saga_rec = ErpSagaExecutionModel(
                tenant_id=self.tenant_id,
                request_id=req_uuid,
                saga_id=saga_id,
                status="started",
                current_step="init",
                idempotency_key=idem_key,
                po_number=po_number,
                payload={
                    "vendor_name": vendor_name,
                    "amount": str(amount),
                    "currency": currency,
                    "department": department,
                },
            )
            session.add(saga_rec)
            await session.flush()

        try:
            # -----------------------------------------------------------------
            # Step 1: Sync or Lookup Vendor in ERP
            # -----------------------------------------------------------------
            vendor_slug = vendor_name.lower().replace(" ", "_").replace(",", "")[:32]
            v_res = await self.connector.sync_vendor(
                ErpVendorPayload(
                    vendor_name=vendor_name,
                    external_id=f"v-{vendor_slug}",
                    currency=currency,
                )
            )
            steps_executed.append("sync_vendor")
            saga_rec.current_step = "vendor_synced"
            saga_rec.status = "vendor_synced"
            await session.flush()

            # -----------------------------------------------------------------
            # NAMED CRASH POINT 1: before_po_creation
            # -----------------------------------------------------------------
            if fault_injection_point == "before_po_creation":
                logger.warning("INJECTING WORKER KILL at crash point: before_po_creation")
                raise CrashSimulationException("before_po_creation")

            # -----------------------------------------------------------------
            # Step 2: Lookup-Before-Retry Purchase Order Creation
            # -----------------------------------------------------------------
            po_payload = ErpPurchaseOrderPayload(
                po_number=po_number,
                request_id=str(req_uuid),
                vendor_id=v_res.vendor_id,
                vendor_name=vendor_name,
                amount=amount,
                currency=currency,
                department=department,
                idempotency_key=f"{self.tenant_id}:{request_id}:po",
                line_items=line_items or [{"desc": "Procurement service", "amount": float(amount)}],
            )
            po_res = await self.connector.create_purchase_order(po_payload)
            steps_executed.append("create_purchase_order")
            saga_rec.current_step = "po_created"
            saga_rec.status = "po_created"
            saga_rec.po_number = po_res.po_number
            await session.flush()

            # -----------------------------------------------------------------
            # NAMED CRASH POINT 2: after_po_creation_before_commitment
            # -----------------------------------------------------------------
            if fault_injection_point == "after_po_creation_before_commitment":
                logger.warning(
                    "INJECTING WORKER KILL at crash point: after_po_creation_before_commitment"
                )
                raise CrashSimulationException("after_po_creation_before_commitment")

            # -----------------------------------------------------------------
            # Step 3: Record Commitment in ERP
            # -----------------------------------------------------------------
            c_payload = ErpCommitmentPayload(
                po_number=po_res.po_number,
                department=department,
                amount=amount,
                currency=currency,
                period="FY2026-Q4",
                idempotency_key=f"{self.tenant_id}:{request_id}:comm",
            )
            await self.connector.record_commitment(c_payload)
            steps_executed.append("record_commitment")
            saga_rec.current_step = "committed"
            saga_rec.status = "committed"
            await session.flush()

            # -----------------------------------------------------------------
            # NAMED CRASH POINT 3: after_commitment_before_finalize
            # -----------------------------------------------------------------
            if fault_injection_point == "after_commitment_before_finalize":
                logger.warning(
                    "INJECTING WORKER KILL at crash point: after_commitment_before_finalize"
                )
                raise CrashSimulationException("after_commitment_before_finalize")

            # -----------------------------------------------------------------
            # Step 4: Finalize Saga and Update Request Record
            # -----------------------------------------------------------------
            saga_rec.current_step = "completed"
            saga_rec.status = "completed"

            # Update request status in Omnea
            await session.execute(
                update(RequestModel)
                .where(
                    and_(
                        RequestModel.tenant_id == self.tenant_id,
                        RequestModel.id == req_uuid,
                    )
                )
                .values(status="approved")
            )
            steps_executed.append("finalize")
            await session.flush()
            await self.connector.circuit_breaker.record_success()

            return ErpSagaResult(
                success=True,
                saga_id=saga_rec.saga_id,
                po_number=po_number,
                status="completed",
                steps_executed=steps_executed,
            )

        except CrashSimulationException:
            # Crash simulations leave DB state persisted for recovery verification
            raise

        except Exception as exc:
            # Handle business/network failure with compensation
            logger.error("Saga step failed with error (%s). Initiating compensation.", exc)
            await self.connector.circuit_breaker.record_failure(exc)
            saga_rec.status = "compensating"
            saga_rec.error_detail = str(exc)
            await session.flush()

            # Compensation: If PO was created, cancel it
            if "create_purchase_order" in steps_executed:
                try:
                    await self.connector.cancel_purchase_order(
                        po_number, reason=f"Saga compensation: {exc}"
                    )
                    saga_rec.compensation_step = "cancel_po_succeeded"
                    saga_rec.status = "compensated"
                except Exception as comp_err:
                    logger.critical("Compensation failed: %s", comp_err)
                    saga_rec.compensation_step = f"cancel_po_failed: {comp_err}"
                    saga_rec.status = "failed"
            else:
                saga_rec.status = "failed"

            await session.flush()
            return ErpSagaResult(
                success=False,
                saga_id=saga_rec.saga_id,
                po_number=po_number,
                status=saga_rec.status,
                steps_executed=steps_executed,
                error=str(exc),
            )
