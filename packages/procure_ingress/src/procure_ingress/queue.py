"""PostgreSQL-backed transactional queue with deduplication, SKIP LOCKED, and backoff."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from procure_core.log_config import get_logger
from procure_ingress.models import InboundEventQueueModel, QueueItem, QueueStatus

logger = get_logger(__name__)


class PostgresInboundQueue:
    """Transactional queue leveraging PostgreSQL FOR UPDATE SKIP LOCKED."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enqueue(
        self,
        tenant_id: str,
        channel: str,
        event_id: str,
        ordering_key: str,
        payload: dict[str, Any],
        normalized_payload: dict[str, Any] | None = None,
    ) -> QueueItem | None:
        """Enqueue an inbound event with atomic deduplication on (channel, event_id).

        Returns QueueItem if newly inserted, or None if the event was already enqueued.
        """
        stmt = (
            insert(InboundEventQueueModel)
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                channel=channel,
                event_id=event_id,
                ordering_key=ordering_key,
                payload=payload,
                normalized_payload=normalized_payload,
                status=QueueStatus.PENDING,
                attempts=0,
                max_attempts=5,
                available_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(index_elements=["channel", "event_id"])
            .returning(InboundEventQueueModel)
        )

        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            logger.info(
                f"Duplicate event ignored: channel={channel}, event_id={event_id}",
                extra={"channel": channel, "event_id": event_id},
            )
            return None

        return QueueItem.model_validate(row)

    async def claim_batch(
        self,
        batch_size: int = 1,
        lock_timeout_seconds: int = 300,
    ) -> list[QueueItem]:
        """Claim a batch of eligible items using FOR UPDATE SKIP LOCKED with lock reclaim."""
        now = datetime.now(UTC)
        lock_expiration = now + timedelta(seconds=lock_timeout_seconds)

        # Reclaim expired locks or select pending items
        query = (
            select(InboundEventQueueModel)
            .where(
                (
                    (InboundEventQueueModel.status == QueueStatus.PENDING)
                    | (
                        (InboundEventQueueModel.status == QueueStatus.PROCESSING)
                        & (InboundEventQueueModel.lock_expires_at < now)
                    )
                )
                & (InboundEventQueueModel.available_at <= now)
            )
            .order_by(InboundEventQueueModel.created_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )

        result = await self.session.execute(query)
        items = list(result.scalars().all())

        claimed: list[QueueItem] = []
        for item in items:
            item.status = QueueStatus.PROCESSING
            item.locked_at = now
            item.lock_expires_at = lock_expiration
            item.attempts += 1
            claimed.append(QueueItem.model_validate(item))

        await self.session.flush()
        return claimed

    async def mark_complete(
        self,
        queue_id: uuid.UUID,
        normalized_payload: dict[str, Any] | None = None,
    ) -> None:
        """Mark an item as successfully completed."""
        now = datetime.now(UTC)
        stmt = (
            select(InboundEventQueueModel)
            .where(InboundEventQueueModel.id == queue_id)
            .with_for_update()
        )
        result = await self.session.execute(stmt)
        item = result.scalar_one_or_none()
        if item:
            item.status = QueueStatus.COMPLETED
            item.completed_at = now
            item.locked_at = None
            item.lock_expires_at = None
            if normalized_payload is not None:
                item.normalized_payload = normalized_payload
            await self.session.flush()

    async def mark_failed(
        self,
        queue_id: uuid.UUID,
        error_message: str,
        base_backoff_seconds: int = 15,
    ) -> None:
        """Mark an item failed, applying exponential backoff or dead-lettering."""
        now = datetime.now(UTC)
        stmt = (
            select(InboundEventQueueModel)
            .where(InboundEventQueueModel.id == queue_id)
            .with_for_update()
        )
        result = await self.session.execute(stmt)
        item = result.scalar_one_or_none()
        if not item:
            return

        item.last_error = error_message
        item.locked_at = None
        item.lock_expires_at = None

        if item.attempts >= item.max_attempts:
            item.status = QueueStatus.DEAD_LETTER
            logger.error(
                f"Queue item {queue_id} moved to dead-letter "
                f"after {item.attempts} attempts: {error_message}"
            )
        else:
            item.status = QueueStatus.PENDING
            delay = base_backoff_seconds * (2 ** (item.attempts - 1))
            item.available_at = now + timedelta(seconds=delay)
            logger.warning(
                f"Queue item {queue_id} retry scheduled in {delay}s "
                f"(attempt {item.attempts}/{item.max_attempts})"
            )

        await self.session.flush()

    async def purge_completed(self, retention_days: int = 7) -> int:
        """Purge completed queue rows older than retention_days."""
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        stmt = delete(InboundEventQueueModel).where(
            (InboundEventQueueModel.status == QueueStatus.COMPLETED)
            & (InboundEventQueueModel.completed_at < cutoff)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        purged = int(getattr(result, "rowcount", 0))
        if purged > 0:
            logger.info(f"Purged {purged} completed queue rows older than {retention_days} days")
        return purged
