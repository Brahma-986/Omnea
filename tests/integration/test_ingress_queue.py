"""Integration tests for PostgresInboundQueue (dedup, claim, backoff, and dead-letter)."""

import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from procure_ingress.models import InboundEventQueueModel, QueueStatus
from procure_ingress.queue import PostgresInboundQueue


@pytest.mark.integration
async def test_queue_deduplication(db_session: AsyncSession) -> None:
    """Verify that inserting duplicate (channel, event_id) is ignored."""
    queue = PostgresInboundQueue(db_session)
    event_id = f"test_ev_{uuid.uuid4()}"

    # First enqueue succeeds
    item1 = await queue.enqueue(
        tenant_id="tenant_1",
        channel="slack",
        event_id=event_id,
        ordering_key="thread_1",
        payload={"msg": "first"},
    )
    assert item1 is not None
    assert item1.event_id == event_id

    # Duplicate enqueue returns None and does not insert
    item2 = await queue.enqueue(
        tenant_id="tenant_1",
        channel="slack",
        event_id=event_id,
        ordering_key="thread_1",
        payload={"msg": "duplicate"},
    )
    assert item2 is None


@pytest.mark.integration
async def test_queue_claim_complete_and_backoff(db_session: AsyncSession) -> None:
    """Verify claiming, completing, and error backoff transition."""
    await db_session.execute(delete(InboundEventQueueModel))
    await db_session.commit()

    queue = PostgresInboundQueue(db_session)
    event_id = f"test_ev_{uuid.uuid4()}"

    item = await queue.enqueue(
        tenant_id="tenant_1",
        channel="slack",
        event_id=event_id,
        ordering_key="thread_1",
        payload={"text": "order 10 monitors"},
    )
    assert item is not None

    # Claim item
    claimed = await queue.claim_batch(batch_size=1, lock_timeout_seconds=60)
    assert len(claimed) == 1
    assert claimed[0].id == item.id
    assert claimed[0].status == QueueStatus.PROCESSING
    assert claimed[0].attempts == 1

    # Mark failed with backoff
    await queue.mark_failed(item.id, "Network timeout during validation")

    # Claim again should yield nothing immediately due to backoff
    claimed_again = await queue.claim_batch(batch_size=1)
    assert len(claimed_again) == 0

    # Mark completed
    await queue.mark_complete(item.id, normalized_payload={"done": True})
