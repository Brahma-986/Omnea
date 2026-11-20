"""Unit tests for Stage 6: ERP Connectors, Circuit Breaker, Rate Limiter, and Idempotent Saga."""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select

from procure_core.db import get_tenant_session
from procure_core.erp.base import CircuitBreaker, TokenBucketRateLimiter
from procure_core.erp.connectors import NetSuiteConnector, SapConnector, WorkdayConnector
from procure_core.erp.ledger import LedgerErpConnector
from procure_core.erp.models import (
    ErpCommitmentPayload,
    ErpPurchaseOrderPayload,
    ErpVendorPayload,
)
from procure_core.erp.saga import ErpSyncSaga
from procure_core.models.erp import (
    ErpLedgerCommitmentModel,
    ErpLedgerPurchaseOrderModel,
    ErpLedgerVendorModel,
    ErpSagaExecutionModel,
)
from procure_core.security.secrets import EncryptedFileSecretsManager


@pytest.mark.asyncio
async def test_circuit_breaker_transitions() -> None:
    """Verify CircuitBreaker trips to OPEN on failures and recovers through HALF_OPEN to CLOSED."""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout_seconds=0.2)
    assert cb.state == "CLOSED"
    assert await cb.can_execute() is True

    # 2 failures do not trip
    await cb.record_failure(RuntimeError("fail 1"))
    await cb.record_failure(RuntimeError("fail 2"))
    assert cb.state == "CLOSED"
    assert await cb.can_execute() is True

    # 3rd failure trips to OPEN
    await cb.record_failure(RuntimeError("fail 3"))
    assert cb.state == "OPEN"
    assert await cb.can_execute() is False

    # Wait for recovery timeout
    await asyncio.sleep(0.25)
    assert await cb.can_execute() is True
    assert cb.state == "HALF_OPEN"

    # Success in HALF_OPEN resets to CLOSED
    await cb.record_success()
    assert cb.state == "CLOSED"
    assert cb.failure_count == 0


@pytest.mark.asyncio
async def test_token_bucket_rate_limiter() -> None:
    """Verify TokenBucketRateLimiter consumes tokens and rejects burst over rate."""
    limiter = TokenBucketRateLimiter(rate=10.0, capacity=2.0)
    assert await limiter.acquire(1.0) is True
    assert await limiter.acquire(1.0) is True
    # Capacity is 2, third immediate acquire without wait should be False
    assert await limiter.acquire(1.0) is False


@pytest.mark.asyncio
async def test_ledger_erp_connector_vendor_sync() -> None:
    """Verify LedgerErpConnector creates and updates vendor idempotently."""
    tenant_id = f"test_tenant_erp_{uuid.uuid4().hex[:8]}"
    connector = LedgerErpConnector(tenant_id=tenant_id)

    v_input = ErpVendorPayload(
        vendor_name="Datadog Technologies Inc",
        external_id="v-datadog-001",
        currency="USD",
    )
    v_res = await connector.sync_vendor(v_input)
    assert v_res.vendor_id is not None
    assert v_res.name == "Datadog Technologies Inc"
    assert v_res.is_new is True

    # Query database directly to verify
    async with get_tenant_session(tenant_id) as session:
        stmt = select(ErpLedgerVendorModel).where(
            ErpLedgerVendorModel.tenant_id == tenant_id,
            ErpLedgerVendorModel.erp_vendor_id == "v-datadog-001",
        )
        res = await session.execute(stmt)
        vendor = res.scalar_one_or_none()
        assert vendor is not None
        assert vendor.name == "Datadog Technologies Inc"

    # Sync again -> should return existing record (is_new=False), idempotent
    v_res2 = await connector.sync_vendor(v_input)
    assert v_res2.vendor_id == v_res.vendor_id
    assert v_res2.is_new is False


@pytest.mark.asyncio
async def test_ledger_erp_connector_po_lookup_before_retry() -> None:
    """Verify PO creation enforces lookup-before-retry, preventing duplicate PO generation."""
    tenant_id = f"test_tenant_erp_{uuid.uuid4().hex[:8]}"
    idem_key = f"po_key_{uuid.uuid4().hex}"
    req_id = str(uuid.uuid4())
    po_num = f"PO-{tenant_id[:4].upper()}-001"

    connector = LedgerErpConnector(tenant_id=tenant_id)
    po_input = ErpPurchaseOrderPayload(
        po_number=po_num,
        request_id=req_id,
        vendor_id="v-101",
        vendor_name="Datadog, Inc.",
        amount=Decimal("48000.00"),
        currency="USD",
        department="Engineering",
        description="Datadog APM Subscription",
        idempotency_key=idem_key,
    )

    # 1. First creation
    record1 = await connector.create_purchase_order(po_input)
    assert record1.po_number == po_num
    assert record1.status == "open"

    # 2. Second creation with SAME idempotency key -> lookup-before-retry returns existing
    record2 = await connector.create_purchase_order(po_input)
    assert record2.po_number == record1.po_number
    assert record2.erp_po_id == record1.erp_po_id

    # Verify from ERP records directly that count is EXACTLY ONE
    orders = await connector.list_purchase_orders()
    matching_pos = [p for p in orders if p.po_number == po_num]
    assert len(matching_pos) == 1
    assert matching_pos[0].po_number == po_num


@pytest.mark.asyncio
async def test_ledger_erp_commitment_and_compensation() -> None:
    """Verify commitment creation and PO cancellation (compensation) in LedgerErpConnector."""
    tenant_id = f"test_tenant_erp_{uuid.uuid4().hex[:8]}"
    po_num = f"PO-COMP-{uuid.uuid4().hex[:8].upper()}"
    po_idem_key = f"po_key_{uuid.uuid4().hex}"
    commit_idem_key = f"commit_key_{uuid.uuid4().hex}"
    req_id = str(uuid.uuid4())

    connector = LedgerErpConnector(tenant_id=tenant_id)

    po_input = ErpPurchaseOrderPayload(
        po_number=po_num,
        request_id=req_id,
        vendor_id="v-102",
        vendor_name="CrowdStrike Inc",
        amount=Decimal("15000.00"),
        currency="EUR",
        department="Security",
        description="CrowdStrike Falcon",
        idempotency_key=po_idem_key,
    )
    po = await connector.create_purchase_order(po_input)
    assert po.status == "open"

    # Record commitment
    commit_input = ErpCommitmentPayload(
        po_number=po.po_number,
        department="Security",
        amount=Decimal("15000.00"),
        currency="EUR",
        period="FY2026-Q4",
        idempotency_key=commit_idem_key,
    )
    commit = await connector.record_commitment(commit_input)
    assert commit.status == "committed"

    # Cancel PO (compensation)
    cancelled = await connector.cancel_purchase_order(
        po.po_number, reason="Approver override rollback"
    )
    assert cancelled is True

    # Verify PO is cancelled and commitment released in database
    async with get_tenant_session(tenant_id) as session:
        po_stmt = select(ErpLedgerPurchaseOrderModel).where(
            ErpLedgerPurchaseOrderModel.tenant_id == tenant_id,
            ErpLedgerPurchaseOrderModel.po_number == po.po_number,
        )
        po_res = await session.execute(po_stmt)
        po_row = po_res.scalar_one()
        assert po_row.status == "cancelled"

        commit_stmt = select(ErpLedgerCommitmentModel).where(
            ErpLedgerCommitmentModel.tenant_id == tenant_id,
            ErpLedgerCommitmentModel.po_number == po.po_number,
        )
        commit_res = await session.execute(commit_stmt)
        commit_row = commit_res.scalar_one()
        assert commit_row.status in ("voided", "released")


@pytest.mark.asyncio
async def test_external_connectors_not_connected_and_credential_resolution() -> None:
    """Verify NetSuite, SAP, and Workday connectors report 'Not connected' without secrets, and resolve when configured."""
    tenant_id = f"test_tenant_ext_{uuid.uuid4().hex[:8]}"

    # 1. Unconfigured connectors
    ns = NetSuiteConnector(tenant_id=tenant_id)
    sap = SapConnector(tenant_id=tenant_id)
    wd = WorkdayConnector(tenant_id=tenant_id)

    res_ns = await ns.test_connection()
    assert res_ns.connected is False
    assert "Not connected" in res_ns.message

    res_sap = await sap.test_connection()
    assert res_sap.connected is False
    assert "Not connected" in res_sap.message

    res_wd = await wd.test_connection()
    assert res_wd.connected is False
    assert "Not connected" in res_wd.message

    # 2. Store credentials in Secrets Manager and verify resolution
    secrets_mgr = EncryptedFileSecretsManager()
    ref = await secrets_mgr.store_secret(
        tenant_id=tenant_id,
        scope="erp",
        name="netsuite_credentials",
        secret_payload={
            "host": "https://api.netsuite.com",
            "account_id": "123456",
            "token": "secret_token_123",
        },
    )

    ns_configured = NetSuiteConnector(tenant_id=tenant_id, secret_ref=ref)
    res_ns_configured = await ns_configured.test_connection()
    assert res_ns_configured.connected is True
    assert res_ns_configured.authenticated_identity == "netsuite://123456"


@pytest.mark.asyncio
async def test_erp_sync_saga_clean_execution_and_idempotency() -> None:
    """Verify ErpSyncSaga executes 4 steps cleanly and is idempotent on duplicate execution."""
    tenant_id = f"test_tenant_saga_{uuid.uuid4().hex[:8]}"
    request_id = str(uuid.uuid4())

    connector = LedgerErpConnector(tenant_id=tenant_id)
    saga = ErpSyncSaga(connector=connector, tenant_id=tenant_id)

    async with get_tenant_session(tenant_id) as session:
        # 1. First execution
        result1 = await saga.execute(
            session=session,
            request_id=request_id,
            vendor_name="Snowflake Inc",
            amount=Decimal("120000.00"),
            currency="USD",
            department="Data Platform",
        )
        await session.commit()

        assert result1.success is True
        assert result1.po_number is not None
        assert result1.status == "completed"
        assert "sync_vendor" in result1.steps_executed
        assert "create_purchase_order" in result1.steps_executed
        assert "record_commitment" in result1.steps_executed
        assert "finalize" in result1.steps_executed

        # 2. Duplicate re-execution with same parameters -> lookup-before-retry ensures same PO returned
        result2 = await saga.execute(
            session=session,
            request_id=request_id,
            vendor_name="Snowflake Inc",
            amount=Decimal("120000.00"),
            currency="USD",
            department="Data Platform",
        )
        await session.commit()

        assert result2.success is True
        assert result2.po_number == result1.po_number
        assert result2.steps_executed == ["idempotency_cache_hit"]

        # Confirm exactly one PO exists in ERP
        all_pos = await connector.list_purchase_orders()
        matching = [p for p in all_pos if p.po_number == result1.po_number]
        assert len(matching) == 1


@pytest.mark.asyncio
async def test_erp_sync_saga_compensation_on_step_failure() -> None:
    """Verify that failure during saga triggers compensation to cancel created PO in ERP."""
    tenant_id = f"test_tenant_saga_comp_{uuid.uuid4().hex[:8]}"
    request_id = str(uuid.uuid4())

    connector = LedgerErpConnector(tenant_id=tenant_id)
    saga = ErpSyncSaga(connector=connector, tenant_id=tenant_id)

    async with get_tenant_session(tenant_id) as session:
        # Simulate exception during commitment step
        with patch.object(
            connector, "record_commitment", side_effect=RuntimeError("Simulated commitment timeout")
        ):
            result = await saga.execute(
                session=session,
                request_id=request_id,
                vendor_name="Figma Inc",
                amount=Decimal("24000.00"),
                currency="USD",
                department="Design",
            )
            await session.commit()

        assert result.success is False
        assert result.status == "compensated"
        assert "Simulated commitment timeout" in (result.error or "")

        # Check saga execution record in database
        stmt = select(ErpSagaExecutionModel).where(
            ErpSagaExecutionModel.tenant_id == tenant_id,
            ErpSagaExecutionModel.request_id == uuid.UUID(request_id),
        )
        res = await session.execute(stmt)
        saga_rec = res.scalar_one_or_none()
        assert saga_rec is not None
        assert saga_rec.status == "compensated"

        # Check that the PO was created then cancelled by compensation
        pos = await connector.list_purchase_orders()
        assert len(pos) == 1
        assert pos[0].status == "cancelled"
