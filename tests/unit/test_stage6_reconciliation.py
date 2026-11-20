"""Unit tests for Stage 6: ERP Reconciliation Engine, Drift Detection, and Audited Repairs."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from procure_core.db import get_tenant_session
from procure_core.erp.ledger import LedgerErpConnector
from procure_core.erp.models import ErpPurchaseOrderPayload
from procure_core.erp.reconciliation import ErpReconciliationService
from procure_core.models.erp import (
    ErpReconciliationFindingModel,
    ErpReconciliationRepairModel,
    ErpReconciliationRunModel,
)
from procure_core.models.finance import BudgetReservationModel
from procure_core.models.request import RequestModel


@pytest.mark.asyncio
async def test_reconciliation_clean_baseline_zero_false_positives() -> None:
    """Verify reconciliation finds zero false positives when Omnea and ERP are in sync."""
    tenant_id = f"test_recon_clean_{uuid.uuid4().hex[:8]}"

    async with get_tenant_session(tenant_id) as session:
        connector = LedgerErpConnector(tenant_id=tenant_id)
        service = ErpReconciliationService(connector=connector, tenant_id=tenant_id)

        # 1. Create approved Omnea request
        req_id = uuid.uuid4()
        req = RequestModel(
            id=req_id,
            tenant_id=tenant_id,
            requester_id=str(uuid.uuid4()),
            status="draft",
            payload={
                "vendor_name": "Atlassian Inc",
                "estimated_spend": "25000.00",
                "currency": "USD",
                "department": "Engineering",
            },
        )
        session.add(req)
        await session.flush()
        req.status = "in_review"
        await session.flush()
        req.status = "approved"
        await session.flush()

        # 2. Create matching ERP Purchase Order
        po_num = f"PO-{tenant_id[:4].upper()}-001"
        await connector.create_purchase_order(
            ErpPurchaseOrderPayload(
                po_number=po_num,
                request_id=str(req_id),
                vendor_id="v-atlassian",
                vendor_name="Atlassian Inc",
                amount=Decimal("25000.00"),
                currency="USD",
                department="Engineering",
                idempotency_key=f"po_{req_id}",
            )
        )

        # 3. Create matching budget reservation
        b_res = BudgetReservationModel(
            tenant_id=tenant_id,
            request_id=req_id,
            department="Engineering",
            amount=Decimal("25000.00"),
            currency="USD",
            period="FY2026",
            status="committed",
            idempotency_key=f"b_res_{uuid.uuid4().hex}",
        )
        session.add(b_res)
        await session.commit()

        # Run reconciliation
        report = await service.run_reconciliation(session)
        await session.commit()

        assert report.status == "clean"
        assert report.drift_count == 0
        assert report.total_checked == 1
        assert len(report.findings) == 0
        assert len(report.alerts_emitted) == 0


@pytest.mark.asyncio
async def test_reconciliation_seeded_drift_detection() -> None:
    """Verify reconciliation detects seeded drift: missing_in_erp, amount_mismatch, status_drift, uncommitted_budget."""
    tenant_id = f"test_recon_drift_{uuid.uuid4().hex[:8]}"

    async with get_tenant_session(tenant_id) as session:
        connector = LedgerErpConnector(tenant_id=tenant_id)
        service = ErpReconciliationService(connector=connector, tenant_id=tenant_id)

        # 1. Seed Drift: missing_in_erp (Approved request with NO PO in ERP)
        req_missing = RequestModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            requester_id=str(uuid.uuid4()),
            status="draft",
            payload={
                "vendor_name": "GitHub Inc",
                "estimated_spend": "18000.00",
                "currency": "USD",
                "department": "Engineering",
            },
        )
        session.add(req_missing)

        # 2. Seed Drift: amount_mismatch (Omnea spend $50k vs ERP PO $40k)
        req_mismatch = RequestModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            requester_id=str(uuid.uuid4()),
            status="draft",
            payload={
                "vendor_name": "Salesforce.com Inc",
                "estimated_spend": "50000.00",
                "currency": "USD",
                "department": "Sales",
            },
        )
        session.add(req_mismatch)

        # 3. Seed Drift: status_drift (ERP PO cancelled, but Omnea request approved)
        req_drift = RequestModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            requester_id=str(uuid.uuid4()),
            status="draft",
            payload={
                "vendor_name": "Zoom Video",
                "estimated_spend": "12000.00",
                "currency": "USD",
                "department": "IT",
            },
        )
        session.add(req_drift)

        # 4. Seed Drift: uncommitted_budget (PO exists in ERP, but no committed budget in Omnea)
        req_uncomm = RequestModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            requester_id=str(uuid.uuid4()),
            status="draft",
            payload={
                "vendor_name": "Miro Software",
                "estimated_spend": "8000.00",
                "currency": "USD",
                "department": "Product",
            },
        )
        session.add(req_uncomm)
        await session.flush()

        # Transition all 4 to in_review then approved
        for r in (req_missing, req_mismatch, req_drift, req_uncomm):
            r.status = "in_review"
        await session.flush()
        for r in (req_missing, req_mismatch, req_drift, req_uncomm):
            r.status = "approved"
        await session.flush()

        # Create PO and budget for mismatch
        await connector.create_purchase_order(
            ErpPurchaseOrderPayload(
                po_number=f"PO-{tenant_id[:4].upper()}-MM",
                request_id=str(req_mismatch.id),
                vendor_id="v-salesforce",
                vendor_name="Salesforce.com Inc",
                amount=Decimal("40000.00"),  # Mismatch
                currency="USD",
                department="Sales",
                idempotency_key=f"po_{req_mismatch.id}",
            )
        )
        session.add(
            BudgetReservationModel(
                tenant_id=tenant_id,
                request_id=req_mismatch.id,
                department="Sales",
                amount=Decimal("50000.00"),
                currency="USD",
                period="FY2026",
                status="committed",
                idempotency_key=f"b_res_{uuid.uuid4().hex}",
            )
        )

        # Create PO and cancel for status_drift
        po_drift = await connector.create_purchase_order(
            ErpPurchaseOrderPayload(
                po_number=f"PO-{tenant_id[:4].upper()}-DR",
                request_id=str(req_drift.id),
                vendor_id="v-zoom",
                vendor_name="Zoom Video",
                amount=Decimal("12000.00"),
                currency="USD",
                department="IT",
                idempotency_key=f"po_{req_drift.id}",
            )
        )
        await connector.cancel_purchase_order(po_drift.po_number, reason="Seeded cancellation")
        session.add(
            BudgetReservationModel(
                tenant_id=tenant_id,
                request_id=req_drift.id,
                department="IT",
                amount=Decimal("12000.00"),
                currency="USD",
                period="FY2026",
                status="committed",
                idempotency_key=f"b_res_{uuid.uuid4().hex}",
            )
        )

        # Create PO for uncommitted (no BudgetReservation)
        await connector.create_purchase_order(
            ErpPurchaseOrderPayload(
                po_number=f"PO-{tenant_id[:4].upper()}-UN",
                request_id=str(req_uncomm.id),
                vendor_id="v-miro",
                vendor_name="Miro Software",
                amount=Decimal("8000.00"),
                currency="USD",
                department="Product",
                idempotency_key=f"po_{req_uncomm.id}",
            )
        )

        await session.commit()

        # Run reconciliation
        report = await service.run_reconciliation(session)
        await session.commit()

        assert report.status == "drift_detected"
        assert report.drift_count == 4
        finding_types = {f["finding_type"] for f in report.findings}
        assert finding_types == {
            "missing_in_erp",
            "amount_mismatch",
            "status_drift",
            "uncommitted_budget",
        }
        assert len(report.alerts_emitted) > 0
        assert report.alerts_emitted[0].alert_type == "erp_drift_detected"


@pytest.mark.asyncio
async def test_reconciliation_repair_authorization_guard() -> None:
    """Verify that unauthorized roles (requester, legal_reviewer) are rejected from repairing ERP drift."""
    tenant_id = f"test_recon_auth_{uuid.uuid4().hex[:8]}"

    async with get_tenant_session(tenant_id) as session:
        connector = LedgerErpConnector(tenant_id=tenant_id)
        service = ErpReconciliationService(connector=connector, tenant_id=tenant_id)

        # Create parent run
        run_rec = ErpReconciliationRunModel(
            tenant_id=tenant_id,
            status="running",
            total_checked=1,
            drift_count=1,
        )
        session.add(run_rec)
        await session.flush()

        # Create finding
        finding = ErpReconciliationFindingModel(
            tenant_id=tenant_id,
            run_id=run_rec.id,
            request_id=uuid.uuid4(),
            po_number="PO-TEST-001",
            finding_type="amount_mismatch",
            severity="high",
            omnea_state={"amount": "1000.00"},
            erp_state={"amount": "900.00"},
        )
        session.add(finding)
        await session.commit()

        # Attempt repair as unauthorized role
        with pytest.raises(PermissionError) as exc_info:
            await service.execute_repair(
                session=session,
                finding_id=str(finding.id),
                repair_action="realign_commitment",
                reason="Attempted unauthorized repair",
                actor_id=str(uuid.uuid4()),
                actor_roles=["requester"],
            )
        assert "lacks finance_admin or tenant_admin permissions" in str(exc_info.value)


@pytest.mark.asyncio
async def test_reconciliation_repair_mandatory_reason_guard() -> None:
    """Verify that repair requires a mandatory audit explanation of at least 5 characters."""
    tenant_id = f"test_recon_reason_{uuid.uuid4().hex[:8]}"

    async with get_tenant_session(tenant_id) as session:
        connector = LedgerErpConnector(tenant_id=tenant_id)
        service = ErpReconciliationService(connector=connector, tenant_id=tenant_id)

        run_rec = ErpReconciliationRunModel(
            tenant_id=tenant_id,
            status="running",
            total_checked=1,
            drift_count=1,
        )
        session.add(run_rec)
        await session.flush()

        finding = ErpReconciliationFindingModel(
            tenant_id=tenant_id,
            run_id=run_rec.id,
            request_id=uuid.uuid4(),
            po_number="PO-TEST-002",
            finding_type="amount_mismatch",
            severity="high",
            omnea_state={"amount": "1000.00"},
            erp_state={"amount": "900.00"},
        )
        session.add(finding)
        await session.commit()

        # Empty reason
        with pytest.raises(ValueError) as exc_info:
            await service.execute_repair(
                session=session,
                finding_id=str(finding.id),
                repair_action="realign_commitment",
                reason="   ",
                actor_id=str(uuid.uuid4()),
                actor_roles=["finance_admin"],
            )
        assert "mandatory audit explanation" in str(exc_info.value)

        # Too short reason
        with pytest.raises(ValueError) as exc_info2:
            await service.execute_repair(
                session=session,
                finding_id=str(finding.id),
                repair_action="realign_commitment",
                reason="fix",
                actor_id=str(uuid.uuid4()),
                actor_roles=["finance_admin"],
            )
        assert "minimum 5 characters" in str(exc_info2.value)


@pytest.mark.asyncio
async def test_reconciliation_repair_execution_and_audit_hash() -> None:
    """Verify authorized repair executes, writes SHA-256 audit hash, and marks finding resolved."""
    tenant_id = f"test_recon_repair_{uuid.uuid4().hex[:8]}"

    async with get_tenant_session(tenant_id) as session:
        connector = LedgerErpConnector(tenant_id=tenant_id)
        service = ErpReconciliationService(connector=connector, tenant_id=tenant_id)

        # 1. Create ERP PO and Finding
        po_num = f"PO-REPAIR-{uuid.uuid4().hex[:8].upper()}"
        req_id = uuid.uuid4()
        await connector.create_purchase_order(
            ErpPurchaseOrderPayload(
                po_number=po_num,
                request_id=str(req_id),
                vendor_id="v-rep",
                vendor_name="Splunk Inc",
                amount=Decimal("30000.00"),
                currency="USD",
                department="Security",
                idempotency_key=f"po_{req_id}",
            )
        )

        run_rec = ErpReconciliationRunModel(
            tenant_id=tenant_id,
            status="running",
            total_checked=1,
            drift_count=1,
        )
        session.add(run_rec)
        await session.flush()

        finding = ErpReconciliationFindingModel(
            tenant_id=tenant_id,
            run_id=run_rec.id,
            request_id=req_id,
            po_number=po_num,
            finding_type="amount_mismatch",
            severity="high",
            omnea_state={"amount": "35000.00", "currency": "USD"},
            erp_state={"amount": "30000.00", "currency": "USD"},
        )
        session.add(finding)
        await session.commit()

        # 2. Authorize and execute repair as finance_admin
        actor_id = str(uuid.uuid4())
        res = await service.execute_repair(
            session=session,
            finding_id=str(finding.id),
            repair_action="realign_commitment",
            reason="Adjusted PO amount per approved amendment AM-104",
            actor_id=actor_id,
            actor_roles=["finance_admin"],
        )
        await session.commit()

        assert res["status"] == "repaired"
        assert res["audit_hash"] is not None
        assert len(res["audit_hash"]) == 64  # Valid SHA-256 hex digest

        # 3. Verify finding record is resolved in DB
        res_finding = await session.get(ErpReconciliationFindingModel, finding.id)
        assert res_finding is not None
        assert res_finding.is_resolved is True
        assert res_finding.resolution_action == "realign_commitment"
        assert res_finding.resolved_by == actor_id

        # 4. Verify audit record written to erp_reconciliation_repairs
        repair_stmt = select(ErpReconciliationRepairModel).where(
            ErpReconciliationRepairModel.tenant_id == tenant_id,
            ErpReconciliationRepairModel.finding_id == finding.id,
        )
        repair_row = (await session.execute(repair_stmt)).scalar_one_or_none()
        assert repair_row is not None
        assert repair_row.audit_hash == res["audit_hash"]
        assert repair_row.reason == "Adjusted PO amount per approved amendment AM-104"

        # 5. Verify ERP PO was updated to 35000.00
        po_in_erp = await connector.get_purchase_order(po_num)
        assert po_in_erp is not None
        assert po_in_erp.amount == Decimal("35000.00")
