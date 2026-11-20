"""ERP Reconciliation Engine, Drift Detection, Audited Repairs, and Finance-Ops Alerts."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from procure_core.erp.base import ErpConnector
from procure_core.erp.saga import CrashSimulationException, ErpSyncSaga
from procure_core.models.erp import (
    ErpLedgerPurchaseOrderModel,
    ErpReconciliationFindingModel,
    ErpReconciliationRepairModel,
    ErpReconciliationRunModel,
)
from procure_core.models.finance import BudgetReservationModel
from procure_core.models.request import RequestModel

logger = logging.getLogger(__name__)


@dataclass
class FinanceOpsAlert:
    """Finance Operations high-priority alert for ERP drift or circuit trips."""

    tenant_id: str
    alert_type: str  # erp_drift_detected, circuit_breaker_tripped, repair_executed
    severity: str  # info, warning, critical
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class ReconciliationReport:
    """Consolidated summary of reconciliation run."""

    run_id: str
    tenant_id: str
    status: str  # clean, drift_detected, failed
    total_checked: int
    drift_count: int
    findings: list[dict[str, Any]]
    alerts_emitted: list[FinanceOpsAlert]


class ErpReconciliationService:
    """Detects and repairs discrepancies between Omnea system of record and ERP ledger."""

    def __init__(self, connector: ErpConnector, tenant_id: str) -> None:
        self.connector = connector
        self.tenant_id = tenant_id

    async def run_reconciliation(self, session: AsyncSession) -> ReconciliationReport:
        """Scan all approved requests and ERP records; zero false positives on clean state."""
        run_rec = ErpReconciliationRunModel(
            tenant_id=self.tenant_id,
            status="running",
            total_checked=0,
            drift_count=0,
        )
        session.add(run_rec)
        await session.flush()

        findings: list[ErpReconciliationFindingModel] = []
        alerts: list[FinanceOpsAlert] = []

        # 1. Fetch all approved Omnea requests
        req_stmt = select(RequestModel).where(
            and_(
                RequestModel.tenant_id == self.tenant_id,
                RequestModel.status == "approved",
            )
        )
        req_rows = (await session.execute(req_stmt)).scalars().all()

        # 2. Fetch all ERP Purchase Orders
        erp_pos = await self.connector.list_purchase_orders(limit=500)
        erp_pos_by_req_id = {po.request_id: po for po in erp_pos}

        # 3. Fetch committed budget reservations
        b_stmt = select(BudgetReservationModel).where(
            and_(
                BudgetReservationModel.tenant_id == self.tenant_id,
                BudgetReservationModel.status == "committed",
            )
        )
        budget_rows = (await session.execute(b_stmt)).scalars().all()
        budgets_by_req_id = {str(b.request_id): b for b in budget_rows}

        total_checked = len(req_rows)

        # Evaluate every approved request
        for req in req_rows:
            req_id_str = str(req.id)
            matching_erp_po = erp_pos_by_req_id.get(req_id_str)

            # Check 1: Missing in ERP
            if not matching_erp_po:
                finding = ErpReconciliationFindingModel(
                    tenant_id=self.tenant_id,
                    run_id=run_rec.id,
                    request_id=req.id,
                    po_number=f"PO-{self.tenant_id[:4].upper()}-{str(req.id)[:8].upper()}",
                    finding_type="missing_in_erp",
                    severity="high",
                    omnea_state={
                        "status": req.status,
                        "amount": str(req.estimated_spend) if req.estimated_spend else "0.00",
                        "currency": req.currency,
                        "vendor_name": req.vendor_name,
                    },
                    erp_state={"status": "not_found", "message": "No purchase order exists in ERP"},
                )
                findings.append(finding)
                session.add(finding)
                continue

            # Check 2: Amount Mismatch
            if req.estimated_spend and matching_erp_po.amount != req.estimated_spend:
                finding = ErpReconciliationFindingModel(
                    tenant_id=self.tenant_id,
                    run_id=run_rec.id,
                    request_id=req.id,
                    po_number=matching_erp_po.po_number,
                    finding_type="amount_mismatch",
                    severity="high",
                    omnea_state={"amount": str(req.estimated_spend), "currency": req.currency},
                    erp_state={
                        "amount": str(matching_erp_po.amount),
                        "currency": matching_erp_po.currency,
                    },
                )
                findings.append(finding)
                session.add(finding)

            # Check 3: Status Drift (e.g. ERP cancelled PO but Omnea approved)
            if matching_erp_po.status == "cancelled" and req.status == "approved":
                finding = ErpReconciliationFindingModel(
                    tenant_id=self.tenant_id,
                    run_id=run_rec.id,
                    request_id=req.id,
                    po_number=matching_erp_po.po_number,
                    finding_type="status_drift",
                    severity="medium",
                    omnea_state={"status": req.status},
                    erp_state={"status": matching_erp_po.status},
                )
                findings.append(finding)
                session.add(finding)

            # Check 4: Uncommitted Budget Reservation
            if req_id_str not in budgets_by_req_id:
                finding = ErpReconciliationFindingModel(
                    tenant_id=self.tenant_id,
                    run_id=run_rec.id,
                    request_id=req.id,
                    po_number=matching_erp_po.po_number,
                    finding_type="uncommitted_budget",
                    severity="medium",
                    omnea_state={"budget_status": "missing_or_uncommitted"},
                    erp_state={"po_status": matching_erp_po.status},
                )
                findings.append(finding)
                session.add(finding)

        # Finalize run
        run_rec.total_checked = total_checked
        run_rec.drift_count = len(findings)
        run_rec.status = "drift_detected" if findings else "clean"
        run_rec.completed_at = datetime.now(UTC)
        await session.flush()

        if findings:
            alert = FinanceOpsAlert(
                tenant_id=self.tenant_id,
                alert_type="erp_drift_detected",
                severity="warning" if len(findings) < 5 else "critical",
                message=f"Reconciliation detected {len(findings)} ERP drift findings for tenant {self.tenant_id}",
                details={"run_id": str(run_rec.id), "drift_count": len(findings)},
            )
            alerts.append(alert)
            logger.warning("Finance-Ops Alert emitted: %s", alert.message)

        return ReconciliationReport(
            run_id=str(run_rec.id),
            tenant_id=self.tenant_id,
            status=run_rec.status,
            total_checked=total_checked,
            drift_count=len(findings),
            findings=[
                {
                    "id": str(f.id),
                    "finding_type": f.finding_type,
                    "po_number": f.po_number,
                    "severity": f.severity,
                    "omnea_state": f.omnea_state,
                    "erp_state": f.erp_state,
                }
                for f in findings
            ],
            alerts_emitted=alerts,
        )

    async def execute_repair(
        self,
        session: AsyncSession,
        finding_id: str,
        repair_action: str,
        reason: str,
        actor_id: str,
        actor_roles: list[str],
        fault_injection_point: str | None = None,
    ) -> dict[str, Any]:
        """Authorized and audited repair of reconciliation drift."""
        # 1. Role Guard: Only finance_admin or tenant_admin can repair ERP drift
        allowed_roles = {"finance_admin", "tenant_admin"}
        if not any(r in allowed_roles for r in actor_roles):
            raise PermissionError(
                f"User '{actor_id}' lacks finance_admin or tenant_admin permissions to repair ERP drift"
            )

        if not reason or len(reason.strip()) < 5:
            raise ValueError(
                "A mandatory audit explanation (minimum 5 characters) is required for ERP repairs."
            )

        # 2. Lookup finding
        f_uuid = uuid.UUID(finding_id)
        stmt = select(ErpReconciliationFindingModel).where(
            and_(
                ErpReconciliationFindingModel.tenant_id == self.tenant_id,
                ErpReconciliationFindingModel.id == f_uuid,
            )
        )
        res = await session.execute(stmt)
        finding = res.scalar_one_or_none()
        if not finding:
            raise ValueError(f"Reconciliation finding '{finding_id}' not found.")

        if finding.is_resolved:
            return {
                "status": "already_resolved",
                "finding_id": finding_id,
                "action": finding.resolution_action,
            }

        # ---------------------------------------------------------------------
        # NAMED CRASH POINT 4: mid_reconciliation_repair
        # ---------------------------------------------------------------------
        if fault_injection_point == "mid_reconciliation_repair":
            logger.warning("INJECTING WORKER KILL at crash point: mid_reconciliation_repair")
            raise CrashSimulationException("mid_reconciliation_repair")

        # 3. Execute repair logic based on finding_type
        if finding.finding_type == "missing_in_erp":
            # Re-run saga to synthesize missing PO
            saga = ErpSyncSaga(connector=self.connector, tenant_id=self.tenant_id)
            omnea_st = finding.omnea_state or {}
            await saga.execute(
                session=session,
                request_id=str(finding.request_id),
                vendor_name=omnea_st.get("vendor_name", "Vendor"),
                amount=Decimal(str(omnea_st.get("amount", "1000.00"))),
                currency=omnea_st.get("currency", "USD"),
            )
        elif finding.finding_type == "amount_mismatch":
            # Realign ERP PO amount to match approved Omnea spend
            omnea_amt = Decimal(str(finding.omnea_state.get("amount", "0.00")))
            await session.execute(
                update(ErpLedgerPurchaseOrderModel)
                .where(
                    and_(
                        ErpLedgerPurchaseOrderModel.tenant_id == self.tenant_id,
                        ErpLedgerPurchaseOrderModel.po_number == finding.po_number,
                    )
                )
                .values(amount=omnea_amt)
            )
        elif finding.finding_type == "status_drift":
            # Resync status: align Omnea request to cancelled
            await session.execute(
                update(RequestModel)
                .where(
                    and_(
                        RequestModel.tenant_id == self.tenant_id,
                        RequestModel.id == finding.request_id,
                    )
                )
                .values(status="cancelled")
            )

        # 4. Record audit hash
        now = datetime.now(UTC)
        audit_raw = (
            f"{self.tenant_id}:{finding_id}:{repair_action}:{actor_id}:{reason}:{now.isoformat()}"
        )
        audit_hash = hashlib.sha256(audit_raw.encode("utf-8")).hexdigest()

        repair_rec = ErpReconciliationRepairModel(
            tenant_id=self.tenant_id,
            finding_id=finding.id,
            repair_action=repair_action,
            reason=reason,
            actor_id=actor_id,
            audit_hash=audit_hash,
            status="completed",
        )
        session.add(repair_rec)

        finding.is_resolved = True
        finding.resolution_action = repair_action
        finding.resolved_by = actor_id
        finding.resolved_at = now
        await session.flush()

        logger.info(
            "ERP drift finding '%s' repaired by %s (action: %s, audit: %s)",
            finding_id,
            actor_id,
            repair_action,
            audit_hash[:12],
        )

        return {
            "status": "repaired",
            "finding_id": finding_id,
            "repair_action": repair_action,
            "audit_hash": audit_hash,
            "resolved_at": now.isoformat(),
        }
