"""Integration tests for vendor merge/unmerge and spend rollup."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from procure_context.context.assembler import ContextAssembler
from procure_context.review.manager import ReviewManager
from procure_core.models.vendor import (
    EntityReviewTaskModel,
    SpendTransactionModel,
    VendorModel,
)


@pytest.mark.asyncio
async def test_merge_and_unmerge_spend_rollup(
    db_session: AsyncSession,
) -> None:
    """Test spend rollup across merge and unmerge lifecycle."""
    tenant_id = f"test-tenant-{uuid.uuid4().hex[:8]}"
    review_mgr = ReviewManager(tenant_id=tenant_id)
    assembler = ContextAssembler(tenant_id=tenant_id, graph_repo=None)

    # 1. Create Vendor A and Vendor B
    vendor_a_id = uuid.uuid4()
    vendor_a = VendorModel(
        id=vendor_a_id,
        tenant_id=tenant_id,
        name="SmallCo Software LLC",
        canonical_key="smallco_software",
        normalized_base="smallco",
        status="active",
    )
    vendor_b_id = uuid.uuid4()
    vendor_b = VendorModel(
        id=vendor_b_id,
        tenant_id=tenant_id,
        name="BigCorp Enterprise Inc",
        canonical_key="bigcorp_enterprise",
        normalized_base="bigcorp",
        status="active",
    )
    db_session.add_all([vendor_a, vendor_b])
    await db_session.flush()

    # 2. Add Spend: Vendor A = $5,000 USD, Vendor B = $10,000 USD
    today = date.today()
    tx_a = SpendTransactionModel(
        tenant_id=tenant_id,
        vendor_id=vendor_a_id,
        amount=Decimal("5000.00"),
        currency="USD",
        transaction_date=today,
        source_system="netsuite",
        source_id=f"tx-{uuid.uuid4().hex[:6]}",
    )
    tx_b = SpendTransactionModel(
        tenant_id=tenant_id,
        vendor_id=vendor_b_id,
        amount=Decimal("10000.00"),
        currency="USD",
        transaction_date=today,
        source_system="sap",
        source_id=f"tx-{uuid.uuid4().hex[:6]}",
    )
    db_session.add_all([tx_a, tx_b])

    # 3. Create review tasks: one between (A, B) and another involving A
    task_ab = EntityReviewTaskModel(
        tenant_id=tenant_id,
        task_type="duplicate_review",
        provisional_vendor_id=vendor_a_id,
        candidate_vendor_id=vendor_b_id,
        source_name="SmallCo",
        confidence=Decimal("0.8500"),
        status="pending",
    )
    # A third vendor to create an additional pending task with A
    vendor_c_id = uuid.uuid4()
    vendor_c = VendorModel(
        id=vendor_c_id,
        tenant_id=tenant_id,
        name="SmallCo Cloud",
        canonical_key="smallco_cloud",
        normalized_base="smallco",
        status="active",
    )
    db_session.add(vendor_c)
    await db_session.flush()

    task_ac = EntityReviewTaskModel(
        tenant_id=tenant_id,
        task_type="duplicate_review",
        provisional_vendor_id=vendor_c_id,
        candidate_vendor_id=vendor_a_id,
        source_name="SmallCo Cloud",
        confidence=Decimal("0.8200"),
        status="pending",
    )
    db_session.add_all([task_ab, task_ac])
    await db_session.flush()

    # 4. Assert separate initial spend rollups
    ctx_a_init = await assembler.assemble_context(db_session, vendor_a_id)
    assert len(ctx_a_init.spend_by_currency) == 1
    assert ctx_a_init.spend_by_currency[0].total_spend == Decimal("5000.00")

    ctx_b_init = await assembler.assemble_context(db_session, vendor_b_id)
    assert len(ctx_b_init.spend_by_currency) == 1
    assert ctx_b_init.spend_by_currency[0].total_spend == Decimal("10000.00")

    # 5. Execute ReviewManager.merge_vendors(A, B)
    merged_vendor = await review_mgr.merge_vendors(
        db_session, source_id=vendor_a_id, target_id=vendor_b_id, actor="ops-engineer@omnea.ai"
    )
    assert merged_vendor.status == "merged"
    assert merged_vendor.merged_into_id == vendor_b_id
    await db_session.flush()

    # 6. Assert B's rolled-up spend becomes USD 15,000
    ctx_b_post_merge = await assembler.assemble_context(db_session, vendor_b_id)
    assert len(ctx_b_post_merge.spend_by_currency) == 1
    assert ctx_b_post_merge.spend_by_currency[0].total_spend == Decimal("15000.00")
    assert ctx_b_post_merge.spend_by_currency[0].transaction_count == 2
    assert vendor_a_id in ctx_b_post_merge.merged_vendor_ids

    # 7. Assert read-time pointer for A resolves to B
    ctx_a_post_merge = await assembler.assemble_context(db_session, vendor_a_id)
    assert ctx_a_post_merge.vendor_id == vendor_b_id
    assert ctx_a_post_merge.vendor_name == "BigCorp Enterprise Inc"
    assert ctx_a_post_merge.spend_by_currency[0].total_spend == Decimal("15000.00")

    # 8. Assert review task for (A, B) is approved_merge and task (C, A) is superseded_by_merge
    q_tab = await db_session.execute(
        select(EntityReviewTaskModel).where(EntityReviewTaskModel.id == task_ab.id)
    )
    reloaded_tab = q_tab.scalar_one()
    assert reloaded_tab.status == "approved_merge"

    q_tac = await db_session.execute(
        select(EntityReviewTaskModel).where(EntityReviewTaskModel.id == task_ac.id)
    )
    reloaded_tac = q_tac.scalar_one()
    assert reloaded_tac.status == "superseded_by_merge"
    assert (reloaded_tac.metadata_ or {}).get("closed_by_merge_of") == str(vendor_a_id)

    # 9. Execute ReviewManager.unmerge_vendors(A)
    unmerged_vendor = await review_mgr.unmerge_vendors(
        db_session, source_id=vendor_a_id, actor="ops-engineer@omnea.ai"
    )
    assert unmerged_vendor.status == "active"
    assert unmerged_vendor.merged_into_id is None
    await db_session.flush()

    # 10. Assert B returns to USD 10,000, A returns to USD 5,000
    ctx_b_post_unmerge = await assembler.assemble_context(db_session, vendor_b_id)
    assert ctx_b_post_unmerge.spend_by_currency[0].total_spend == Decimal("10000.00")
    assert vendor_a_id not in ctx_b_post_unmerge.merged_vendor_ids

    ctx_a_post_unmerge = await assembler.assemble_context(db_session, vendor_a_id)
    assert ctx_a_post_unmerge.vendor_id == vendor_a_id
    assert ctx_a_post_unmerge.vendor_name == "SmallCo Software LLC"
    assert ctx_a_post_unmerge.spend_by_currency[0].total_spend == Decimal("5000.00")

    # 11. Assert superseded review task (C, A) is restored back to pending
    q_tac_restored = await db_session.execute(
        select(EntityReviewTaskModel).where(EntityReviewTaskModel.id == task_ac.id)
    )
    reloaded_tac_restored = q_tac_restored.scalar_one()
    assert reloaded_tac_restored.status == "pending"
    assert "closed_by_merge_of" not in (reloaded_tac_restored.metadata_ or {})
