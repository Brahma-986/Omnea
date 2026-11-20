"""Integration tests for Stage 6: Crash-Grade Worker Kill Tests at Four Named Points.

Guarantees:
1. Kill at 'before_po_creation' -> recovery creates exactly 1 PO.
2. Kill at 'after_po_creation_before_commitment' -> recovery uses lookup-before-retry, avoids double PO, creates commitment.
3. Kill at 'after_commitment_before_finalize' -> recovery confirms commitment, finalizes status.
4. Kill at 'mid_reconciliation_repair' -> recovery completes repair idempotently.
5. In all cases, querying `ledger_erp.list_purchase_orders()` yields count == 1.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from procure_core.db import get_tenant_session
from procure_core.erp.ledger import LedgerErpConnector
from procure_core.erp.models import ErpPurchaseOrderPayload
from procure_core.erp.reconciliation import ErpReconciliationService
from procure_core.erp.saga import CrashSimulationException, ErpSyncSaga
from procure_core.models.erp import (
    ErpLedgerCommitmentModel,
    ErpReconciliationFindingModel,
    ErpReconciliationRunModel,
)
from procure_core.models.request import RequestModel


@pytest.mark.asyncio
async def test_worker_kill_point_1_before_po_creation() -> None:
    """CRASH POINT 1: Kill before PO creation -> recovery creates exactly 1 PO."""
    tenant_id = f"test_crash1_{uuid.uuid4().hex[:8]}"
    request_id = str(uuid.uuid4())
    req_uuid = uuid.UUID(request_id)
    connector = LedgerErpConnector(tenant_id=tenant_id)

    async with get_tenant_session(tenant_id) as session:
        # Create request in draft, transition to in_review
        req = RequestModel(
            id=req_uuid,
            tenant_id=tenant_id,
            requester_id=str(uuid.uuid4()),
            status="draft",
            payload={
                "vendor_name": "Datadog, Inc.",
                "estimated_spend": "48000.00",
                "currency": "USD",
                "department": "Engineering",
            },
        )
        session.add(req)
        await session.flush()
        req.status = "in_review"
        await session.flush()
        await session.commit()

    # 1. First execution: Inject worker kill before PO creation
    saga = ErpSyncSaga(connector=connector, tenant_id=tenant_id)
    with pytest.raises(CrashSimulationException) as exc_info:
        async with get_tenant_session(tenant_id) as session:
            await saga.execute(
                session=session,
                request_id=request_id,
                vendor_name="Datadog, Inc.",
                amount=Decimal("48000.00"),
                currency="USD",
                department="Engineering",
                fault_injection_point="before_po_creation",
            )
            await session.commit()

    assert exc_info.value.point_name == "before_po_creation"

    # Verify state at crash: zero POs created yet
    pos_at_crash = await connector.list_purchase_orders()
    assert len(pos_at_crash) == 0

    # 2. Worker Restart / Recovery: Re-run saga without fault injection
    async with get_tenant_session(tenant_id) as session:
        recovery_result = await saga.execute(
            session=session,
            request_id=request_id,
            vendor_name="Datadog, Inc.",
            amount=Decimal("48000.00"),
            currency="USD",
            department="Engineering",
        )
        await session.commit()

    assert recovery_result.success is True
    assert recovery_result.status == "completed"

    # SOX Invariant: Exactly ONE PO created in ERP's own records
    pos_after_recovery = await connector.list_purchase_orders()
    assert len(pos_after_recovery) == 1
    assert pos_after_recovery[0].po_number == recovery_result.po_number
    assert pos_after_recovery[0].amount == Decimal("48000.00")


@pytest.mark.asyncio
async def test_worker_kill_point_2_after_po_creation_before_commitment() -> None:
    """CRASH POINT 2: Kill after PO creation before commitment -> lookup-before-retry avoids double PO."""
    tenant_id = f"test_crash2_{uuid.uuid4().hex[:8]}"
    request_id = str(uuid.uuid4())
    req_uuid = uuid.UUID(request_id)
    connector = LedgerErpConnector(tenant_id=tenant_id)

    async with get_tenant_session(tenant_id) as session:
        req = RequestModel(
            id=req_uuid,
            tenant_id=tenant_id,
            requester_id=str(uuid.uuid4()),
            status="draft",
            payload={
                "vendor_name": "Datadog, Inc.",
                "estimated_spend": "48000.00",
                "currency": "USD",
                "department": "Engineering",
            },
        )
        session.add(req)
        await session.flush()
        req.status = "in_review"
        await session.flush()
        await session.commit()

    # 1. First execution: Inject worker kill after PO creation, before commitment
    saga = ErpSyncSaga(connector=connector, tenant_id=tenant_id)
    with pytest.raises(CrashSimulationException) as exc_info:
        async with get_tenant_session(tenant_id) as session:
            await saga.execute(
                session=session,
                request_id=request_id,
                vendor_name="Datadog, Inc.",
                amount=Decimal("48000.00"),
                currency="USD",
                department="Engineering",
                fault_injection_point="after_po_creation_before_commitment",
            )
            await session.commit()

    assert exc_info.value.point_name == "after_po_creation_before_commitment"

    # Verify state at crash: Exactly 1 PO exists in ERP, but commitment not yet written
    pos_at_crash = await connector.list_purchase_orders()
    assert len(pos_at_crash) == 1
    first_po_number = pos_at_crash[0].po_number

    # 2. Worker Restart / Recovery: Re-run saga
    # Must use lookup-before-retry and NOT create a duplicate PO
    async with get_tenant_session(tenant_id) as session:
        recovery_result = await saga.execute(
            session=session,
            request_id=request_id,
            vendor_name="Datadog, Inc.",
            amount=Decimal("48000.00"),
            currency="USD",
            department="Engineering",
        )
        await session.commit()

    assert recovery_result.success is True
    assert recovery_result.status == "completed"
    assert recovery_result.po_number == first_po_number

    # CRITICAL SOX ACCEPTANCE INVARIANT:
    # Query ERP's own tables directly: count MUST BE EXACTLY ONE (no double-creation)!
    all_erp_pos = await connector.list_purchase_orders()
    assert len(all_erp_pos) == 1
    assert all_erp_pos[0].po_number == first_po_number

    # Commitment is now completed
    async with get_tenant_session(tenant_id) as session:
        comm_stmt = select(ErpLedgerCommitmentModel).where(
            ErpLedgerCommitmentModel.tenant_id == tenant_id,
            ErpLedgerCommitmentModel.po_number == first_po_number,
        )
        comm_row = (await session.execute(comm_stmt)).scalar_one_or_none()
        assert comm_row is not None
        assert comm_row.status == "committed"


@pytest.mark.asyncio
async def test_worker_kill_point_3_after_commitment_before_finalize() -> None:
    """CRASH POINT 3: Kill after commitment before finalize -> recovery finalizes status, exactly 1 PO."""
    tenant_id = f"test_crash3_{uuid.uuid4().hex[:8]}"
    request_id = str(uuid.uuid4())
    req_uuid = uuid.UUID(request_id)
    connector = LedgerErpConnector(tenant_id=tenant_id)

    async with get_tenant_session(tenant_id) as session:
        req = RequestModel(
            id=req_uuid,
            tenant_id=tenant_id,
            requester_id=str(uuid.uuid4()),
            status="draft",
            payload={
                "vendor_name": "Datadog, Inc.",
                "estimated_spend": "48000.00",
                "currency": "USD",
                "department": "Engineering",
            },
        )
        session.add(req)
        await session.flush()
        req.status = "in_review"
        await session.flush()
        await session.commit()

    # 1. First execution: Inject worker kill after commitment, before finalize
    saga = ErpSyncSaga(connector=connector, tenant_id=tenant_id)
    with pytest.raises(CrashSimulationException) as exc_info:
        async with get_tenant_session(tenant_id) as session:
            await saga.execute(
                session=session,
                request_id=request_id,
                vendor_name="Datadog, Inc.",
                amount=Decimal("48000.00"),
                currency="USD",
                department="Engineering",
                fault_injection_point="after_commitment_before_finalize",
            )
            await session.commit()

    assert exc_info.value.point_name == "after_commitment_before_finalize"

    # Verify state at crash: 1 PO exists
    pos_at_crash = await connector.list_purchase_orders()
    assert len(pos_at_crash) == 1

    # 2. Worker Restart / Recovery: Re-run saga
    async with get_tenant_session(tenant_id) as session:
        recovery_result = await saga.execute(
            session=session,
            request_id=request_id,
            vendor_name="Datadog, Inc.",
            amount=Decimal("48000.00"),
            currency="USD",
            department="Engineering",
        )
        await session.commit()

    assert recovery_result.success is True
    assert recovery_result.status == "completed"

    # Query ERP records: exactly 1 PO
    all_erp_pos = await connector.list_purchase_orders()
    assert len(all_erp_pos) == 1
    assert all_erp_pos[0].po_number == recovery_result.po_number

    # Request status in Omnea is finalized to approved
    async with get_tenant_session(tenant_id) as session:
        req_row = await session.get(RequestModel, req_uuid)
        assert req_row is not None
        assert req_row.status == "approved"


@pytest.mark.asyncio
async def test_worker_kill_point_4_mid_reconciliation_repair() -> None:
    """CRASH POINT 4: Kill mid-reconciliation repair -> recovery completes repair idempotently."""
    tenant_id = f"test_crash4_{uuid.uuid4().hex[:8]}"
    connector = LedgerErpConnector(tenant_id=tenant_id)
    service = ErpReconciliationService(connector=connector, tenant_id=tenant_id)

    po_num = f"PO-CRASH4-{uuid.uuid4().hex[:8].upper()}"
    req_id = uuid.uuid4()

    async with get_tenant_session(tenant_id) as session:
        # Create ERP PO with amount 30000.00
        await connector.create_purchase_order(
            ErpPurchaseOrderPayload(
                po_number=po_num,
                request_id=str(req_id),
                vendor_id="v-crash4",
                vendor_name="Datadog, Inc.",
                amount=Decimal("30000.00"),
                currency="USD",
                department="Engineering",
                idempotency_key=f"po_{req_id}",
            )
        )

        # Create finding with Omnea spend 48000.00
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
            omnea_state={"amount": "48000.00", "currency": "USD"},
            erp_state={"amount": "30000.00", "currency": "USD"},
        )
        session.add(finding)
        await session.commit()
        finding_id_str = str(finding.id)

    # 1. First execution: Inject worker kill mid-repair
    actor_id = str(uuid.uuid4())
    with pytest.raises(CrashSimulationException) as exc_info:
        async with get_tenant_session(tenant_id) as session:
            await service.execute_repair(
                session=session,
                finding_id=finding_id_str,
                repair_action="realign_commitment",
                reason="Aligning with approved contract value",
                actor_id=actor_id,
                actor_roles=["finance_admin"],
                fault_injection_point="mid_reconciliation_repair",
            )
            await session.commit()

    assert exc_info.value.point_name == "mid_reconciliation_repair"

    # Verify state at crash: finding is not resolved
    async with get_tenant_session(tenant_id) as session:
        f_mid = await session.get(ErpReconciliationFindingModel, uuid.UUID(finding_id_str))
        assert f_mid is not None
        assert f_mid.is_resolved is False

    # 2. Worker Restart / Recovery: Re-run repair without fault injection
    async with get_tenant_session(tenant_id) as session:
        repair_res = await service.execute_repair(
            session=session,
            finding_id=finding_id_str,
            repair_action="realign_commitment",
            reason="Aligning with approved contract value",
            actor_id=actor_id,
            actor_roles=["finance_admin"],
        )
        await session.commit()

    assert repair_res["status"] == "repaired"
    assert repair_res["audit_hash"] is not None

    # Verify finding is now marked resolved
    async with get_tenant_session(tenant_id) as session:
        f_final = await session.get(ErpReconciliationFindingModel, uuid.UUID(finding_id_str))
        assert f_final is not None
        assert f_final.is_resolved is True
        assert f_final.resolution_action == "realign_commitment"

    # SOX INVARIANT: Count POs in ERP's own records -> strictly 1
    erp_pos = await connector.list_purchase_orders()
    matching_pos = [p for p in erp_pos if p.po_number == po_num]
    assert len(matching_pos) == 1
    assert matching_pos[0].amount == Decimal("48000.00")
