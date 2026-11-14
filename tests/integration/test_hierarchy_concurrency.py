"""Integration tests for vendor hierarchy trigger: cycle prevention and depth limit <= 4."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from procure_core.config import get_settings


@pytest.fixture
async def direct_session_factory():
    settings = get_settings()
    engine = create_async_engine(settings.async_database_url, echo=False)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_hierarchy_cycle_rejection_direct(direct_session_factory) -> None:
    """Test immediate cycle rejection (self-parent and A->B->A)."""
    tenant_id = f"tenant_cycle_{uuid.uuid4().hex[:8]}"
    v1_id = uuid.uuid4()
    v2_id = uuid.uuid4()

    async with direct_session_factory() as session:
        # Create V1 and V2
        await session.execute(
            text("""
                INSERT INTO vendors (id, tenant_id, canonical_key, name, normalized_base, status)
                VALUES
                    (:v1, :t, 'v1', 'Vendor 1', 'v1', 'active'),
                    (:v2, :t, 'v2', 'Vendor 2', 'v2', 'active');
            """),
            {"v1": v1_id, "v2": v2_id, "t": tenant_id},
        )
        await session.commit()

        # 1. Self-parent rejection
        with pytest.raises(Exception, match=r"Vendor cannot be its own parent"):
            await session.execute(
                text(
                    "UPDATE vendors SET parent_vendor_id = :v1 WHERE tenant_id = :t AND id = :v1;"
                ),
                {"v1": v1_id, "t": tenant_id},
            )
        await session.rollback()

        # 2. Set V2 parent = V1 (depth 2)
        await session.execute(
            text("UPDATE vendors SET parent_vendor_id = :v1 WHERE tenant_id = :t AND id = :v2;"),
            {"v1": v1_id, "v2": v2_id, "t": tenant_id},
        )
        await session.commit()

        # 3. Attempt V1 parent = V2 -> Cycle!
        with pytest.raises(Exception, match=r"Hierarchy cycle detected"):
            await session.execute(
                text(
                    "UPDATE vendors SET parent_vendor_id = :v2 WHERE tenant_id = :t AND id = :v1;"
                ),
                {"v1": v1_id, "v2": v2_id, "t": tenant_id},
            )
        await session.rollback()


@pytest.mark.asyncio
async def test_hierarchy_concurrent_ab_ba_cycle(direct_session_factory) -> None:
    """Test concurrent A->B and B->A serialize on hierarchy lock and reject cycle."""
    tenant_id = f"tenant_conc_{uuid.uuid4().hex[:8]}"
    v_a = uuid.uuid4()
    v_b = uuid.uuid4()

    async with direct_session_factory() as session:
        await session.execute(
            text("""
                INSERT INTO vendors (id, tenant_id, canonical_key, name, normalized_base, status)
                VALUES
                    (:va, :t, 'va', 'Vendor A', 'va', 'active'),
                    (:vb, :t, 'vb', 'Vendor B', 'vb', 'active');
            """),
            {"va": v_a, "vb": v_b, "t": tenant_id},
        )
        await session.commit()

    barrier = asyncio.Barrier(2)
    results: list[str] = []

    async def tx_a_to_b() -> None:
        async with direct_session_factory() as session:
            await barrier.wait()
            try:
                await session.execute(
                    text(
                        "UPDATE vendors SET parent_vendor_id = :vb "
                        "WHERE tenant_id = :t AND id = :va;"
                    ),
                    {"va": v_a, "vb": v_b, "t": tenant_id},
                )
                await session.commit()
                results.append("A->B committed")
            except Exception as e:
                results.append(f"A->B error: {e}")
                await session.rollback()

    async def tx_b_to_a() -> None:
        async with direct_session_factory() as session:
            await barrier.wait()
            try:
                await session.execute(
                    text(
                        "UPDATE vendors SET parent_vendor_id = :va "
                        "WHERE tenant_id = :t AND id = :vb;"
                    ),
                    {"va": v_a, "vb": v_b, "t": tenant_id},
                )
                await session.commit()
                results.append("B->A committed")
            except Exception as e:
                results.append(f"B->A error: {e}")
                await session.rollback()

    await asyncio.gather(tx_a_to_b(), tx_b_to_a())

    # Exactly one must succeed, and the second must be rejected with hierarchy cycle detected
    assert len(results) == 2
    successes = [r for r in results if "committed" in r]
    rejections = [r for r in results if "Hierarchy cycle detected" in r]
    assert len(successes) == 1, f"Expected 1 commit, got: {results}"
    assert len(rejections) == 1, f"Expected 1 cycle rejection, got: {results}"


@pytest.mark.asyncio
async def test_hierarchy_depth_5_reparent_rejection(direct_session_factory) -> None:
    """Test that re-parenting a subtree that would result in total depth > 4 is rejected."""
    tenant_id = f"tenant_depth_{uuid.uuid4().hex[:8]}"

    # Ancestor chain: V1 (depth 1) -> V2 (depth 2) -> V3 (depth 3)
    v1 = uuid.uuid4()
    v2 = uuid.uuid4()
    v3 = uuid.uuid4()

    # Subtree: V4 (depth 1) -> V5 (depth 2)
    v4 = uuid.uuid4()
    v5 = uuid.uuid4()

    async with direct_session_factory() as session:
        # Create all nodes
        await session.execute(
            text("""
                INSERT INTO vendors (id, tenant_id, canonical_key, name, normalized_base, status)
                VALUES
                    (:v1, :t, 'v1', 'V1', 'v1', 'active'),
                    (:v2, :t, 'v2', 'V2', 'v2', 'active'),
                    (:v3, :t, 'v3', 'V3', 'v3', 'active'),
                    (:v4, :t, 'v4', 'V4', 'v4', 'active'),
                    (:v5, :t, 'v5', 'V5', 'v5', 'active');
            """),
            {"v1": v1, "v2": v2, "v3": v3, "v4": v4, "v5": v5, "t": tenant_id},
        )
        await session.commit()

        # Build ancestor chain: V1 is root, V2 under V1, V3 under V2 (depth 3)
        await session.execute(
            text("UPDATE vendors SET parent_vendor_id = :v1 WHERE tenant_id = :t AND id = :v2;"),
            {"v1": v1, "v2": v2, "t": tenant_id},
        )
        await session.execute(
            text("UPDATE vendors SET parent_vendor_id = :v2 WHERE tenant_id = :t AND id = :v3;"),
            {"v2": v2, "v3": v3, "t": tenant_id},
        )

        # Build subtree: V5 under V4 (depth 2 within subtree)
        await session.execute(
            text("UPDATE vendors SET parent_vendor_id = :v4 WHERE tenant_id = :t AND id = :v5;"),
            {"v4": v4, "v5": v5, "t": tenant_id},
        )
        await session.commit()

        # Re-parent V4 under V3:
        # Ancestor depth above V3 = 3 (V1, V2, V3)
        # Subtree depth below V4 = 1 (V5)
        # Total depth = 3 + 1 + 1 = 5 > 4!
        with pytest.raises(Exception, match=r"Hierarchy depth limit exceeded: total depth 5 > 4"):
            await session.execute(
                text(
                    "UPDATE vendors SET parent_vendor_id = :v3 WHERE tenant_id = :t AND id = :v4;"
                ),
                {"v3": v3, "v4": v4, "t": tenant_id},
            )
        await session.rollback()
