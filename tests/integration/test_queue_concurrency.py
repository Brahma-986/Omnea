"""Integration test verifying FOR UPDATE SKIP LOCKED mutual exclusion under concurrent consumers."""

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from procure_ingress.models import QueueItem
from procure_ingress.queue import PostgresInboundQueue


@pytest.mark.integration
async def test_two_consumer_concurrency(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify that two concurrent consumers claiming items via SKIP LOCKED
    never claim duplicate items.
    """
    total_items = 10
    event_ids: list[str] = [f"conc_ev_{uuid.uuid4()}" for _ in range(total_items)]

    # 1. Enqueue items
    async with session_factory() as session:
        queue = PostgresInboundQueue(session)
        for eid in event_ids:
            await queue.enqueue(
                tenant_id="tenant_concurrency",
                channel="slack",
                event_id=eid,
                ordering_key="thread_conc",
                payload={"index": eid},
            )
        await session.commit()

    # 2. Spawn two concurrent consumers with separate DB sessions
    async def consumer_task() -> list[QueueItem]:
        async with session_factory() as worker_session:
            worker_queue = PostgresInboundQueue(worker_session)
            # Claim up to half the items
            claimed = await worker_queue.claim_batch(batch_size=5, lock_timeout_seconds=60)
            await worker_session.commit()
            return claimed

    results = await asyncio.gather(
        consumer_task(),
        consumer_task(),
    )

    claimed_1, claimed_2 = results
    ids_1 = {item.id for item in claimed_1}
    ids_2 = {item.id for item in claimed_2}

    # Mutual exclusion check: No item must be claimed by both workers
    assert ids_1.isdisjoint(ids_2), f"Overlap detected between workers: {ids_1.intersection(ids_2)}"
    assert len(ids_1) + len(ids_2) == total_items, f"Expected {total_items} items claimed in total"
