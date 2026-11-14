"""Integration tests for CSV and ERP batch ingestion connectors."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from procure_context.connectors.csv_connector import CsvConnector
from procure_context.connectors.erp_mock import ErpMockConnector
from procure_core.models.vendor import SpendTransactionModel, VendorModel


@pytest.mark.asyncio
async def test_csv_ingestion_with_column_mapping_and_idempotency(
    db_session: AsyncSession,
) -> None:
    """CSV ingestion resolves entities, updates spend ledger idempotently, and logs provenance."""
    tenant_id = f"test-tenant-{uuid.uuid4().hex[:8]}"

    # 1. Seed existing parent vendor in DB
    parent_id = uuid.uuid4()
    parent = VendorModel(
        id=parent_id,
        tenant_id=tenant_id,
        name="Datadog, Inc.",
        canonical_key="datadog",
        normalized_base="datadog",
        status="active",
    )
    db_session.add(parent)
    await db_session.flush()

    # 2. Prepare custom CSV data
    csv_data = """Supplier Name,Invoice Amount,Currency,Date,Invoice ID,Spend Category
"Datadog, Inc.",15000.00,USD,2026-04-01,INV-1001,Cloud Monitoring
Datadog EMEA,8000.00,EUR,2026-04-02,INV-1002,APM Tools
Unknown Software Labs LLC,3500.00,USD,2026-04-03,INV-1003,Dev Tools
"""
    col_mapping = {
        "vendor_name": "Supplier Name",
        "amount": "Invoice Amount",
        "currency": "Currency",
        "transaction_date": "Date",
        "source_id": "Invoice ID",
        "category": "Spend Category",
    }

    connector = CsvConnector(
        tenant_id=tenant_id,
        source_system="test_csv_feed",
        column_mapping=col_mapping,
    )

    # 3. Dry-run first
    res_dry = await connector.ingest_csv(db_session, csv_data, dry_run=True)
    assert res_dry.dry_run is True
    assert res_dry.total_rows == 3
    assert res_dry.processed_rows == 3
    assert res_dry.matched_count == 1  # Datadog, Inc.
    assert res_dry.subsidiary_count == 1  # Datadog EMEA -> subsidiary of Datadog
    assert res_dry.novel_count == 1  # Unknown Software Labs LLC
    assert res_dry.total_spend_by_currency["USD"] == Decimal("18500.00")
    assert res_dry.total_spend_by_currency["EUR"] == Decimal("8000.00")

    # Assert dry run committed ZERO rows
    q_dry = await db_session.execute(
        select(func.count()).where(SpendTransactionModel.tenant_id == tenant_id)
    )
    assert q_dry.scalar() == 0

    # 4. Actual ingestion run
    res_live = await connector.ingest_csv(db_session, csv_data, dry_run=False)
    assert res_live.dry_run is False
    assert res_live.processed_rows == 3

    # Assert 3 spend transactions exist in DB
    q_live = await db_session.execute(
        select(SpendTransactionModel).where(SpendTransactionModel.tenant_id == tenant_id)
    )
    txs = q_live.scalars().all()
    assert len(txs) == 3

    tx_map = {t.source_id: t for t in txs}
    assert "INV-1001" in tx_map
    assert tx_map["INV-1001"].amount == Decimal("15000.0000")
    assert tx_map["INV-1001"].vendor_id == parent_id
    assert tx_map["INV-1001"].source_system == "test_csv_feed"
    assert tx_map["INV-1001"].provenance["raw_data"]["Supplier Name"] == "Datadog, Inc."

    assert "INV-1002" in tx_map
    assert tx_map["INV-1002"].amount == Decimal("8000.0000")
    assert tx_map["INV-1002"].currency == "EUR"
    # Resolved to parent because it is a subsidiary of Datadog
    assert tx_map["INV-1002"].vendor_id == parent_id

    assert "INV-1003" in tx_map
    # Created a new vendor for Unknown Software Labs
    novel_tx = tx_map["INV-1003"]
    assert novel_tx.amount == Decimal("3500.0000")
    assert novel_tx.vendor_id != parent_id

    # 5. Idempotent rerun: re-running the same CSV should NOT create duplicate transactions
    res_rerun = await connector.ingest_csv(db_session, csv_data, dry_run=False)
    assert res_rerun.processed_rows == 3

    q_rerun = await db_session.execute(
        select(func.count()).where(SpendTransactionModel.tenant_id == tenant_id)
    )
    assert q_rerun.scalar() == 3  # Count unchanged!


@pytest.mark.asyncio
async def test_erp_mock_connector_payload(
    db_session: AsyncSession,
) -> None:
    """ERP mock connector processes structured payloads with resolution and idempotency."""
    tenant_id = f"test-tenant-{uuid.uuid4().hex[:8]}"

    # Seed vendor
    vendor_id = uuid.uuid4()
    vendor = VendorModel(
        id=vendor_id,
        tenant_id=tenant_id,
        name="Workday, Inc.",
        canonical_key="workday",
        normalized_base="workday",
        status="active",
    )
    db_session.add(vendor)
    await db_session.flush()

    erp_payload = [
        {
            "document_id": "SAP-9001",
            "supplier_name": "Workday, Inc.",
            "total_amount": "25000.00",
            "currency": "USD",
            "posting_date": "2026-05-10",
            "cost_center": "HR",
        },
        {
            "document_id": "SAP-9002",
            "supplier_name": "FreshBooks LLC",
            "total_amount": "1200.00",
            "currency": "USD",
            "posting_date": "2026-05-11",
            "cost_center": "Finance",
        },
    ]

    erp_connector = ErpMockConnector(
        tenant_id=tenant_id,
        source_system="sap_ariba",
    )

    res = await erp_connector.ingest_payload(db_session, erp_payload, dry_run=False)
    assert res.total_rows == 2
    assert res.processed_rows == 2
    assert res.matched_count == 1  # Workday
    assert res.total_spend_by_currency["USD"] == Decimal("26200.00")

    # Verify DB persistence
    q_tx = await db_session.execute(
        select(SpendTransactionModel).where(
            SpendTransactionModel.tenant_id == tenant_id,
            SpendTransactionModel.source_id == "SAP-9001",
        )
    )
    tx = q_tx.scalar_one()
    assert tx.vendor_id == vendor_id
    assert tx.source_system == "sap_ariba"
    assert tx.category == "HR"
