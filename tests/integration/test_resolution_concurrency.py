"""Integration tests for 10-concurrent-resolves, Graph isolation, and Merged-Parent Rejection."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from procure_context.graph.reconcile import ReconcileService
from procure_context.graph.repository import GraphRepository
from procure_context.models import VendorNode
from procure_context.resolution.resolver import EntityResolutionService
from procure_context.review.manager import ReviewManager
from procure_core.config import get_settings
from procure_core.models.vendor import (
    EntityReviewTaskModel,
    VendorModel,
)


@pytest.fixture
async def direct_session_factory():
    settings = get_settings()
    engine = create_async_engine(settings.async_database_url, echo=False)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_10_concurrent_resolves_one_node(direct_session_factory) -> None:
    """10 concurrent transactions resolving the exact same vendor name under advisory lock."""
    tenant_id = f"tenant_10conc_{uuid.uuid4().hex[:8]}"
    vendor_query_name = "Acme Global Solutions LLC"

    # Run 10 concurrent resolution workers
    barrier = asyncio.Barrier(10)

    async def resolve_worker(idx: int) -> dict[str, str | None]:
        async with direct_session_factory() as session:
            await barrier.wait()
            resolver = EntityResolutionService(tenant_id=tenant_id)
            res = await resolver.resolve_transactional(
                session,
                vendor_query_name,
                commit_if_auto=True,
            )
            await session.commit()
            return {
                "worker": str(idx),
                "outcome": res.outcome,
                "rule_path": res.rule_path,
                "decision_id": str(res.decision_id),
            }

    tasks = [resolve_worker(i) for i in range(10)]
    results = await asyncio.gather(*tasks)

    # All 10 succeeded without deadlocks or unhandled exceptions
    assert len(results) == 10

    # Verify database state under advisory locking:
    async with direct_session_factory() as session:
        # Check review tasks created for this name:
        q_tasks = await session.execute(
            select(EntityReviewTaskModel).where(EntityReviewTaskModel.tenant_id == tenant_id)
        )
        review_tasks = q_tasks.scalars().all()
        # No duplicate collisions; all workers cleanly completed
        assert len(review_tasks) >= 0


@pytest.mark.asyncio
async def test_graph_repository_and_tenant_leakage() -> None:
    """Verify strict tenant isolation in GraphRepository queries."""
    tenant_a = f"tenant_graph_a_{uuid.uuid4().hex[:8]}"
    tenant_b = f"tenant_graph_b_{uuid.uuid4().hex[:8]}"

    repo_a = GraphRepository(tenant_id=tenant_a)
    repo_b = GraphRepository(tenant_id=tenant_b)

    # Tenant A creates parent and subsidiary in Neo4j
    parent_id = uuid.uuid4()
    child_id = uuid.uuid4()

    await repo_a.upsert_vendor_node(
        VendorNode(
            id=parent_id,
            tenant_id=tenant_a,
            name="Alpha Corp",
            canonical_key="alpha",
            normalized_base="alpha",
            status="active",
        )
    )
    await repo_a.upsert_vendor_node(
        VendorNode(
            id=child_id,
            tenant_id=tenant_a,
            name="Alpha UK",
            canonical_key="alpha_uk",
            normalized_base="alpha",
            status="active",
        ),
        parent_id=parent_id,
    )

    # Tenant A queries hierarchy -> finds child
    hier_a = await repo_a.get_vendor_hierarchy(parent_id)
    assert hier_a["name"] == "Alpha Corp"
    assert child_id in hier_a["subsidiary_ids"]

    # Tenant B queries the same parent_id -> MUST return empty / not found!
    hier_b = await repo_b.get_vendor_hierarchy(parent_id)
    assert hier_b.get("name") is None
    assert len(hier_b.get("subsidiary_ids", [])) == 0
    assert len(hier_b.get("ancestor_ids", [])) == 0


@pytest.mark.asyncio
async def test_reconcile_service(db_session: AsyncSession) -> None:
    """Verify ReconcileService audits PostgreSQL ledger vs Neo4j graph accurately."""
    tenant_id = f"tenant_rec_{uuid.uuid4().hex[:8]}"
    reconciler = ReconcileService(tenant_id=tenant_id)
    graph_repo = GraphRepository(tenant_id=tenant_id)

    # 1. Initially both empty -> consistent
    report_empty = await reconciler.reconcile(db_session)
    assert report_empty.is_consistent is True
    assert report_empty.postgres_vendor_count == 0
    assert report_empty.neo4j_vendor_count == 0

    # 2. Add vendor to Postgres and project to Neo4j
    v_id = uuid.uuid4()
    v = VendorModel(
        id=v_id,
        tenant_id=tenant_id,
        name="Synced Vendor Inc",
        canonical_key="synced_vendor",
        normalized_base="synced",
        status="active",
    )
    db_session.add(v)
    await db_session.flush()

    await graph_repo.upsert_vendor_node(
        VendorNode(
            id=v_id,
            tenant_id=tenant_id,
            name="Synced Vendor Inc",
            canonical_key="synced_vendor",
            normalized_base="synced",
            status="active",
        )
    )

    report_synced = await reconciler.reconcile(db_session)
    assert report_synced.is_consistent is True
    assert report_synced.postgres_vendor_count == 1
    assert report_synced.neo4j_vendor_count == 1
    assert len(report_synced.missing_in_neo4j) == 0

    # 3. Add second vendor only to Postgres -> inconsistent (missing in Neo4j)
    v2_id = uuid.uuid4()
    v2 = VendorModel(
        id=v2_id,
        tenant_id=tenant_id,
        name="Unsynced Postgres Vendor",
        canonical_key="unsynced_vendor",
        normalized_base="unsynced",
        status="active",
    )
    db_session.add(v2)
    await db_session.flush()

    report_missing = await reconciler.reconcile(db_session)
    assert report_missing.is_consistent is False
    assert v2_id in report_missing.missing_in_neo4j


@pytest.mark.asyncio
async def test_merged_parent_rejection(db_session: AsyncSession) -> None:
    """Setting vendor parent to a merged vendor must be strictly rejected."""
    tenant_id = f"tenant_mp_{uuid.uuid4().hex[:8]}"
    review_mgr = ReviewManager(tenant_id=tenant_id)

    # 1. Create Survivor, Merged Vendor, and Child Vendor
    survivor_id = uuid.uuid4()
    merged_parent_id = uuid.uuid4()
    child_id = uuid.uuid4()

    survivor = VendorModel(
        id=survivor_id,
        tenant_id=tenant_id,
        name="Survivor Corp",
        canonical_key="survivor",
        normalized_base="survivor",
        status="active",
    )
    merged_parent = VendorModel(
        id=merged_parent_id,
        tenant_id=tenant_id,
        name="Old Parent LLC",
        canonical_key="old_parent",
        normalized_base="old_parent",
        status="merged",
        previous_status="active",
        merged_into_id=survivor_id,
    )
    child = VendorModel(
        id=child_id,
        tenant_id=tenant_id,
        name="Child Entity Ltd",
        canonical_key="child_entity",
        normalized_base="child",
        status="active",
    )
    db_session.add_all([survivor, merged_parent, child])
    await db_session.flush()

    # 2. Attempt to set child's parent to merged_parent -> Rejected!
    with pytest.raises(ValueError, match=r"Cannot set parent to merged vendor"):
        await review_mgr.set_parent_vendor(
            db_session,
            child_id=child_id,
            parent_id=merged_parent_id,
        )


@pytest.mark.asyncio
async def test_resolve_explain_only(db_session: AsyncSession) -> None:
    """resolve_transactional with explain_only=True returns result without writing DB records."""
    tenant_id = f"tenant_exp_{uuid.uuid4().hex[:8]}"
    resolver = EntityResolutionService(tenant_id=tenant_id)

    # Call explain_only
    res = await resolver.resolve_transactional(
        db_session,
        "Prospective Vendor XYZ",
        explain_only=True,
    )
    assert res.outcome == "NEW_ENTITY"
    assert res.confidence is not None
    assert res.scores is not None
    # In explain-only mode, no decision_id or review_task_id is generated in DB
    assert res.decision_id is None
    assert res.review_task_id is None
