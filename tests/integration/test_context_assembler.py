"""Integration tests for Enterprise Context Assembler."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from procure_context.context.assembler import ContextAssembler
from procure_core.models.intent import ProcurementIntentModel
from procure_core.models.vendor import (
    ContractModel,
    SpendTransactionModel,
    VendorModel,
    VendorSecurityReviewModel,
)


@pytest.mark.asyncio
async def test_context_assembler_spend_rollup_multi_currency(
    db_session: AsyncSession,
) -> None:
    """Verify spend rollup groups strictly per-currency across hierarchy and merged nodes."""
    tenant_id = f"test-tenant-{uuid.uuid4().hex[:8]}"
    assembler = ContextAssembler(tenant_id=tenant_id, graph_repo=None)

    # 1. Create Parent Vendor
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

    # 2. Create Subsidiary Vendor
    sub_id = uuid.uuid4()
    subsidiary = VendorModel(
        id=sub_id,
        tenant_id=tenant_id,
        name="Datadog EMEA",
        canonical_key="datadog_emea",
        normalized_base="datadog",
        region="emea",
        parent_vendor_id=parent_id,
        status="active",
    )
    db_session.add(subsidiary)

    # 3. Create Merged Vendor
    merged_id = uuid.uuid4()
    merged = VendorModel(
        id=merged_id,
        tenant_id=tenant_id,
        name="Datadog Legacy SAS",
        canonical_key="datadog_legacy",
        normalized_base="datadog",
        status="merged",
        previous_status="active",
        merged_into_id=parent_id,
    )
    db_session.add(merged)
    await db_session.flush()

    today = date.today()

    # 4. Add spend transactions across all three vendors in multiple currencies
    txs = [
        # Parent spend: 5000 USD, 2000 USD, 3000 EUR
        SpendTransactionModel(
            tenant_id=tenant_id,
            vendor_id=parent_id,
            amount=Decimal("5000.00"),
            currency="USD",
            transaction_date=today - timedelta(days=30),
            source_system="sap",
            source_id="tx-p-1",
        ),
        SpendTransactionModel(
            tenant_id=tenant_id,
            vendor_id=parent_id,
            amount=Decimal("2000.00"),
            currency="USD",
            transaction_date=today - timedelta(days=20),
            source_system="sap",
            source_id="tx-p-2",
        ),
        SpendTransactionModel(
            tenant_id=tenant_id,
            vendor_id=parent_id,
            amount=Decimal("3000.00"),
            currency="EUR",
            transaction_date=today - timedelta(days=15),
            source_system="sap",
            source_id="tx-p-3",
        ),
        # Subsidiary spend: 4000 USD, 1500 GBP
        SpendTransactionModel(
            tenant_id=tenant_id,
            vendor_id=sub_id,
            amount=Decimal("4000.00"),
            currency="USD",
            transaction_date=today - timedelta(days=10),
            source_system="netsuite",
            source_id="tx-s-1",
        ),
        SpendTransactionModel(
            tenant_id=tenant_id,
            vendor_id=sub_id,
            amount=Decimal("1500.00"),
            currency="GBP",
            transaction_date=today - timedelta(days=5),
            source_system="netsuite",
            source_id="tx-s-2",
        ),
        # Merged vendor spend: 1000 USD
        SpendTransactionModel(
            tenant_id=tenant_id,
            vendor_id=merged_id,
            amount=Decimal("1000.00"),
            currency="USD",
            transaction_date=today - timedelta(days=40),
            source_system="legacy_csv",
            source_id="tx-m-1",
        ),
    ]
    db_session.add_all(txs)

    # 5. Add Active Contract & Expired Contract
    contract_active = ContractModel(
        tenant_id=tenant_id,
        vendor_id=parent_id,
        title="Enterprise MSA 2026",
        status="active",
        total_value=Decimal("100000.00"),
        currency="USD",
        start_date=today - timedelta(days=100),
        end_date=today + timedelta(days=265),
        auto_renew=True,
    )
    contract_expired = ContractModel(
        tenant_id=tenant_id,
        vendor_id=parent_id,
        title="Pilot Contract 2025",
        status="active",  # marked active but expired end_date
        total_value=Decimal("10000.00"),
        currency="USD",
        start_date=today - timedelta(days=400),
        end_date=today - timedelta(days=35),
    )
    db_session.add_all([contract_active, contract_expired])

    # 6. Add Security Reviews (one fresh SOC2, one expired ISO27001)
    sec_fresh = VendorSecurityReviewModel(
        tenant_id=tenant_id,
        vendor_id=parent_id,
        doc_type="SOC2_TYPE_II",
        status="compliant",
        effective_date=today - timedelta(days=60),
        expiration_date=today + timedelta(days=300),
    )
    sec_expired = VendorSecurityReviewModel(
        tenant_id=tenant_id,
        vendor_id=parent_id,
        doc_type="ISO_27001",
        status="compliant",
        effective_date=today - timedelta(days=400),
        expiration_date=today - timedelta(days=10),
    )
    db_session.add_all([sec_fresh, sec_expired])
    await db_session.flush()

    # 7. Assemble context for parent vendor
    context = await assembler.assemble_context(db_session, parent_id)

    # Verify hierarchy
    assert context.vendor_id == parent_id
    assert context.vendor_name == "Datadog, Inc."
    assert sub_id in context.subsidiary_vendor_ids
    assert merged_id in context.merged_vendor_ids

    # Verify spend rollup strictly per-currency
    spend_map = {s.currency: s for s in context.spend_by_currency}
    assert len(spend_map) == 3
    # USD: 5000 + 2000 + 4000 + 1000 = 12000 USD across 4 transactions
    assert spend_map["USD"].total_spend == Decimal("12000.00")
    assert spend_map["USD"].transaction_count == 4
    # EUR: 3000 EUR across 1 transaction
    assert spend_map["EUR"].total_spend == Decimal("3000.00")
    assert spend_map["EUR"].transaction_count == 1
    # GBP: 1500 GBP across 1 transaction
    assert spend_map["GBP"].total_spend == Decimal("1500.00")
    assert spend_map["GBP"].transaction_count == 1

    # Verify active contracts (expired one must NOT be returned)
    assert len(context.active_contracts) == 1
    assert context.active_contracts[0].title == "Enterprise MSA 2026"
    assert context.active_contracts[0].total_value == Decimal("100000.00")

    # Verify security reviews freshness
    assert len(context.security_reviews) == 2
    soc2 = next(r for r in context.security_reviews if r.doc_type == "SOC2_TYPE_II")
    iso = next(r for r in context.security_reviews if r.doc_type == "ISO_27001")
    assert soc2.is_fresh is True
    assert iso.is_fresh is False

    # Verify citations exist for every section
    source_tables = {c["source_table"] for c in context.citations}
    assert "vendors" in source_tables
    assert "spend_transactions" in source_tables
    assert "contracts" in source_tables
    assert "vendor_security_reviews" in source_tables


@pytest.mark.asyncio
async def test_context_assembler_follows_merge_pointer(
    db_session: AsyncSession,
) -> None:
    """Querying context for a merged vendor follows pointer to active survivor."""
    tenant_id = f"test-tenant-{uuid.uuid4().hex[:8]}"
    assembler = ContextAssembler(tenant_id=tenant_id, graph_repo=None)

    survivor_id = uuid.uuid4()
    survivor = VendorModel(
        id=survivor_id,
        tenant_id=tenant_id,
        name="Salesforce, Inc.",
        canonical_key="salesforce",
        normalized_base="salesforce",
        status="active",
    )
    db_session.add(survivor)

    merged_id = uuid.uuid4()
    merged = VendorModel(
        id=merged_id,
        tenant_id=tenant_id,
        name="Slack Technologies LLC",
        canonical_key="slack",
        normalized_base="slack",
        status="merged",
        previous_status="active",
        merged_into_id=survivor_id,
    )
    db_session.add(merged)
    await db_session.flush()

    # Querying merged vendor should resolve to survivor
    context = await assembler.assemble_context(db_session, merged_id)
    assert context.vendor_id == survivor_id
    assert context.vendor_name == "Salesforce, Inc."
    assert merged_id in context.merged_vendor_ids

    # Citation must explicitly record merge pointer redirection
    merge_cites = [c for c in context.citations if c.get("requested_vendor_id") == str(merged_id)]
    assert len(merge_cites) == 1
    assert merge_cites[0]["effective_vendor_id"] == str(survivor_id)


@pytest.mark.asyncio
async def test_context_assembler_for_intent(
    db_session: AsyncSession,
) -> None:
    """Assembling context for a ProcurementIntent resolves vendor or marks novel."""
    tenant_id = f"test-tenant-{uuid.uuid4().hex[:8]}"
    assembler = ContextAssembler(tenant_id=tenant_id, graph_repo=None)

    # 1. Existing vendor in DB
    vendor_id = uuid.uuid4()
    vendor = VendorModel(
        id=vendor_id,
        tenant_id=tenant_id,
        name="Snowflake Inc.",
        canonical_key="snowflake",
        normalized_base="snowflake",
        status="active",
    )
    db_session.add(vendor)
    await db_session.flush()

    intent_match = ProcurementIntentModel(
        id=uuid.uuid4(),
        session_id="sess-1",
        tenant_id=tenant_id,
        vendor_name="Snowflake Inc.",
        model="gemini-2.5-pro",
        prompt_version="1.0",
        intent_type="software",
    )
    context_match = await assembler.assemble_context_for_intent(db_session, intent_match)
    assert context_match.vendor_id == vendor_id
    assert context_match.vendor_name == "Snowflake Inc."

    # 2. Novel vendor not in catalog
    intent_novel = ProcurementIntentModel(
        id=uuid.uuid4(),
        session_id="sess-2",
        tenant_id=tenant_id,
        vendor_name="Brand New Startup XYZ",
        model="gemini-2.5-pro",
        prompt_version="1.0",
        intent_type="software",
    )
    context_novel = await assembler.assemble_context_for_intent(db_session, intent_novel)
    assert context_novel.status == "novel_entity"
    assert context_novel.vendor_name == "Brand New Startup XYZ"


@pytest.mark.asyncio
async def test_context_assembler_fallbacks_and_metadata(
    db_session: AsyncSession,
) -> None:
    """Verify assembler returns none_recorded/not_checked/unknown and hierarchy_source."""
    tenant_id = f"test-tenant-{uuid.uuid4().hex[:8]}"
    assembler = ContextAssembler(tenant_id=tenant_id, graph_repo=None)

    # 1. Bare vendor with no spend, no contracts, and no security reviews
    bare_vendor_id = uuid.uuid4()
    bare_vendor = VendorModel(
        id=bare_vendor_id,
        tenant_id=tenant_id,
        name="Empty Vendor Corp",
        canonical_key="empty_vendor",
        normalized_base="empty",
        status="active",
    )
    db_session.add(bare_vendor)
    await db_session.flush()

    ctx = await assembler.assemble_context(db_session, bare_vendor_id)
    assert ctx.hierarchy_source == "postgres_authoritative"
    assert ctx.projection_lag == 0
    assert len(ctx.spend_by_currency) == 0
    assert len(ctx.active_contracts) == 0
    assert len(ctx.security_reviews) == 0

    facts = [c["fact"] for c in ctx.citations]
    assert any("Spend: none_recorded" in f for f in facts)
    assert any("Contracts: none_recorded" in f for f in facts)
    assert any("Security reviews: not_checked" in f for f in facts)

    for c in ctx.citations:
        assert "as_of" in c
        assert "source_table" in c

    # 2. Add security review with missing expiration / audit period -> unknown freshness
    sec_unknown = VendorSecurityReviewModel(
        tenant_id=tenant_id,
        vendor_id=bare_vendor_id,
        doc_type="SOC2_TYPE_II",
        status="pending_review",
        effective_date=date.today(),
        expiration_date=date.today() - timedelta(days=1),  # or None if allowed, but expired status
        metadata_={},
    )
    db_session.add(sec_unknown)
    await db_session.flush()

    ctx2 = await assembler.assemble_context(db_session, bare_vendor_id)
    assert len(ctx2.security_reviews) == 1
    assert ctx2.security_reviews[0].is_fresh is False
