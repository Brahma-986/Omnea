"""Integration tests for Neo4j Outbox worker, projection, and event replay."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from procure_context.graph.client import execute_cypher_read
from procure_context.graph.outbox_worker import OutboxWorker
from procure_context.graph.repository import GraphRepository
from procure_core.models.vendor import Neo4jOutboxModel


@pytest.mark.asyncio
async def test_outbox_worker_projection_to_neo4j(
    db_session: AsyncSession,
) -> None:
    """Outbox worker claims events, projects to Neo4j, and marks status processed."""
    tenant_id = f"test-tenant-{uuid.uuid4().hex[:8]}"
    worker = OutboxWorker(tenant_id=tenant_id)
    repo = GraphRepository(tenant_id=tenant_id)

    parent_id = uuid.uuid4()
    child_id = uuid.uuid4()

    # 1. Enqueue vendor_created events in outbox
    ev1 = Neo4jOutboxModel(
        tenant_id=tenant_id,
        aggregate_id=str(parent_id),
        ordering_key=f"vendor:{parent_id}",
        idempotency_key=f"create:{parent_id}",
        event_type="vendor_created",
        aggregate_version=1,
        payload={
            "id": str(parent_id),
            "name": "Acme Global Corp",
            "canonical_key": "acme_global",
            "normalized_base": "acme",
            "status": "active",
        },
    )
    ev2 = Neo4jOutboxModel(
        tenant_id=tenant_id,
        aggregate_id=str(child_id),
        ordering_key=f"vendor:{child_id}",
        idempotency_key=f"create:{child_id}",
        event_type="vendor_created",
        aggregate_version=1,
        payload={
            "id": str(child_id),
            "name": "Acme UK Ltd",
            "canonical_key": "acme_uk",
            "normalized_base": "acme",
            "region": "uk",
            "parent_id": str(parent_id),
            "status": "active",
        },
    )
    db_session.add_all([ev1, ev2])
    await db_session.flush()

    # 2. Process batch
    processed = await worker.process_batch(db_session, limit=10)
    assert processed == 2

    # 3. Verify in PostgreSQL that outbox rows are processed
    q = await db_session.execute(
        select(Neo4jOutboxModel).where(Neo4jOutboxModel.tenant_id == tenant_id)
    )
    rows = q.scalars().all()
    assert all(r.status == "processed" for r in rows)
    assert all(r.processed_at is not None for r in rows)

    # 4. Verify in Neo4j that nodes and hierarchy exist
    hier = await repo.get_vendor_hierarchy(child_id)
    assert hier["vendor_id"] == child_id
    assert hier["parent_id"] == parent_id

    # Check Cypher directly
    cypher = """
    MATCH (c:Vendor {tenant_id: $tenant_id, id: $child_id})
          -[:SUBSIDIARY_OF]->(p:Vendor {tenant_id: $tenant_id, id: $parent_id})
    RETURN c.name AS child_name, p.name AS parent_name;
    """
    neo_res = await execute_cypher_read(
        cypher,
        {"tenant_id": tenant_id, "child_id": str(child_id), "parent_id": str(parent_id)},
    )
    assert len(neo_res) == 1
    assert neo_res[0]["child_name"] == "Acme UK Ltd"
    assert neo_res[0]["parent_name"] == "Acme Global Corp"


@pytest.mark.asyncio
async def test_outbox_worker_deadletter_blocking_and_replay(
    db_session: AsyncSession,
) -> None:
    """Failed events dead-letter, blocking subsequent events on same key until replayed."""
    tenant_id = f"test-tenant-{uuid.uuid4().hex[:8]}"

    # Mock repository that fails for vendor_merged but succeeds for others
    failing_repo = AsyncMock()
    failing_repo.merge_vendor_nodes.side_effect = RuntimeError("Simulated Neo4j connection failure")
    failing_repo.upsert_vendor_node.return_value = None

    worker = OutboxWorker(tenant_id=tenant_id, graph_repo_factory=lambda _tid: failing_repo)

    vendor_x = uuid.uuid4()
    vendor_y = uuid.uuid4()

    # Event 1: will fail (vendor_merged)
    ev_fail = Neo4jOutboxModel(
        tenant_id=tenant_id,
        aggregate_id=str(vendor_x),
        ordering_key=f"vendor:{vendor_x}",
        idempotency_key=f"fail:{uuid.uuid4().hex[:6]}",
        event_type="vendor_merged",
        max_attempts=1,  # Set max_attempts=1 so it immediately enters failed status
        payload={"source_id": str(vendor_x), "target_id": str(vendor_y)},
    )
    # Event 2: valid event on the SAME ordering key
    ev_blocked = Neo4jOutboxModel(
        tenant_id=tenant_id,
        aggregate_id=str(vendor_x),
        ordering_key=f"vendor:{vendor_x}",
        idempotency_key=f"blocked:{uuid.uuid4().hex[:6]}",
        event_type="vendor_created",
        payload={
            "id": str(vendor_x),
            "name": "Vendor X",
            "canonical_key": "vendor_x",
            "normalized_base": "vendor_x",
        },
    )
    db_session.add_all([ev_fail, ev_blocked])
    await db_session.flush()

    # Process batch 1: ev_fail fails and enters dead-letter
    await worker.process_batch(db_session, limit=10)

    await db_session.refresh(ev_fail)
    await db_session.refresh(ev_blocked)

    assert ev_fail.status == "dead_letter"
    assert "Simulated Neo4j connection failure" in (ev_fail.last_error or "")
    # ev_blocked must NOT have been processed!
    assert ev_blocked.status == "pending"

    # Process batch 2: ev_blocked is still blocked because ev_fail on same ordering_key is failed
    processed_2 = await worker.process_batch(db_session, limit=10)
    assert processed_2 == 0
    await db_session.refresh(ev_blocked)
    assert ev_blocked.status == "pending"

    # Now replay events for vendor_x
    replayed = await worker.replay_events(
        db_session, tenant_id=tenant_id, aggregate_id=str(vendor_x)
    )
    assert replayed == 1
    await db_session.refresh(ev_fail)
    assert ev_fail.status == "pending"
    assert ev_fail.attempts == 0

    # Fix mock so it succeeds now
    failing_repo.merge_vendor_nodes.side_effect = None
    failing_repo.merge_vendor_nodes.return_value = None

    # Process batch 3: both events should now process in order!
    processed_3 = await worker.process_batch(db_session, limit=10)
    assert processed_3 == 2

    await db_session.refresh(ev_fail)
    await db_session.refresh(ev_blocked)
    assert ev_fail.status == "processed"
    assert ev_blocked.status == "processed"
