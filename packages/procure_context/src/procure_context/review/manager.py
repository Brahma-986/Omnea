"""Review task and reversible vendor merge/unmerge manager."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from procure_core.models.vendor import (
    EntityReviewTaskModel,
    Neo4jOutboxModel,
    VendorModel,
)

logger = logging.getLogger(__name__)


class ReviewManager:
    """Manages entity review tasks, reversible merges, and outbox event propagation."""

    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id

    async def merge_vendors(
        self,
        session: AsyncSession,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
        actor: str,
    ) -> VendorModel:
        """Execute reversible vendor merge with task superseding and outbox projection."""
        if source_id == target_id:
            raise ValueError(f"Cannot merge vendor {source_id} into itself")

        # 1. Acquire Family Advisory Locks for both vendors (in sorted order to prevent deadlocks)
        q_v = await session.execute(
            select(VendorModel).where(
                and_(
                    VendorModel.tenant_id == self.tenant_id,
                    VendorModel.id.in_([source_id, target_id]),
                )
            )
        )
        vendors = {v.id: v for v in q_v.scalars().all()}
        if source_id not in vendors:
            raise ValueError(f"Source vendor {source_id} not found in tenant {self.tenant_id}")
        if target_id not in vendors:
            raise ValueError(f"Target vendor {target_id} not found in tenant {self.tenant_id}")

        source = vendors[source_id]
        target = vendors[target_id]

        if source.status == "merged":
            raise ValueError(
                f"Source vendor {source_id} is already merged into {source.merged_into_id}"
            )
        if target.status == "merged":
            raise ValueError(f"Cannot merge into an already merged vendor {target_id}")

        # Sorted locking on normalized_base
        lock_keys = sorted(
            [
                f"{self.tenant_id}:family:{source.normalized_base}",
                f"{self.tenant_id}:family:{target.normalized_base}",
            ]
        )
        for key in lock_keys:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": key},
            )

        # Hierarchy advisory lock
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"{self.tenant_id}:hierarchy"},
        )

        # 2. Update source vendor status to 'merged'
        source.previous_status = source.status
        source.status = "merged"
        source.merged_into_id = target_id
        session.add(source)

        # 3. Handle review tasks
        # (source, target) task becomes approved_merge
        await session.execute(
            update(EntityReviewTaskModel)
            .where(
                and_(
                    EntityReviewTaskModel.tenant_id == self.tenant_id,
                    EntityReviewTaskModel.provisional_vendor_id == source_id,
                    EntityReviewTaskModel.candidate_vendor_id == target_id,
                    EntityReviewTaskModel.status == "pending",
                )
            )
            .values(
                status="approved_merge",
                resolved_by=actor,
                resolved_at=text("now()"),
            )
        )

        # Other pending tasks involving source get superseded_by_merge with metadata tag
        q_other_tasks = await session.execute(
            select(EntityReviewTaskModel).where(
                and_(
                    EntityReviewTaskModel.tenant_id == self.tenant_id,
                    EntityReviewTaskModel.status == "pending",
                    (
                        (EntityReviewTaskModel.provisional_vendor_id == source_id)
                        | (EntityReviewTaskModel.candidate_vendor_id == source_id)
                    ),
                )
            )
        )
        for task in q_other_tasks.scalars().all():
            task.status = "superseded_by_merge"
            task.resolved_by = actor
            task.resolved_at = datetime.now(UTC)
            task_meta = dict(task.metadata_ or {})
            task_meta["closed_by_merge_of"] = str(source_id)
            task.metadata_ = task_meta
            session.add(task)

        # 4. Enqueue Neo4j Outbox merge event
        outbox = Neo4jOutboxModel(
            tenant_id=self.tenant_id,
            aggregate_id=str(source_id),
            ordering_key=f"vendor:{source_id}",
            idempotency_key=f"merge:{source_id}:{target_id}:{uuid.uuid4().hex[:8]}",
            event_type="vendor_merged",
            payload={
                "source_id": str(source_id),
                "target_id": str(target_id),
                "actor": actor,
            },
        )
        session.add(outbox)
        await session.flush()
        return source

    async def unmerge_vendors(
        self,
        session: AsyncSession,
        source_id: uuid.UUID,
        actor: str,
    ) -> VendorModel:
        """Reversibly unmerge vendor, restoring original status and restoring superseded tasks."""
        q_v = await session.execute(
            select(VendorModel).where(
                and_(
                    VendorModel.tenant_id == self.tenant_id,
                    VendorModel.id == source_id,
                )
            )
        )
        source = q_v.scalar_one_or_none()
        if not source:
            raise ValueError(f"Vendor {source_id} not found")
        if source.status != "merged" or not source.merged_into_id:
            raise ValueError(f"Vendor {source_id} is not merged")

        target_id = source.merged_into_id

        # Advisory locks
        lock_keys = sorted(
            [
                f"{self.tenant_id}:family:{source.normalized_base}",
                f"{self.tenant_id}:hierarchy",
            ]
        )
        for key in lock_keys:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": key},
            )

        # 1. Restore vendor status
        source.status = source.previous_status or "active"
        source.previous_status = None
        source.merged_into_id = None
        session.add(source)

        # 2. Restore tasks closed by this merge
        q_tasks = await session.execute(
            select(EntityReviewTaskModel).where(
                and_(
                    EntityReviewTaskModel.tenant_id == self.tenant_id,
                    EntityReviewTaskModel.status == "superseded_by_merge",
                )
            )
        )
        for task in q_tasks.scalars().all():
            meta: dict[str, Any] = task.metadata_ or {}
            if meta.get("closed_by_merge_of") == str(source_id):
                task.status = "pending"
                task.resolved_by = None
                task.resolved_at = None
                meta.pop("closed_by_merge_of", None)
                task.metadata_ = meta
                session.add(task)

        # 3. Enqueue Neo4j Outbox unmerge event
        outbox = Neo4jOutboxModel(
            tenant_id=self.tenant_id,
            aggregate_id=str(source_id),
            ordering_key=f"vendor:{source_id}",
            idempotency_key=f"unmerge:{source_id}:{uuid.uuid4().hex[:8]}",
            event_type="vendor_unmerged",
            payload={
                "source_id": str(source_id),
                "target_id": str(target_id),
                "actor": actor,
            },
        )
        session.add(outbox)
        await session.flush()
        return source

    async def set_parent_vendor(
        self,
        session: AsyncSession,
        child_id: uuid.UUID,
        parent_id: uuid.UUID | None,
    ) -> VendorModel:
        """Set vendor parent with merged-parent rejection and hierarchy advisory lock."""
        if parent_id is not None:
            if child_id == parent_id:
                raise ValueError(f"Vendor {child_id} cannot be its own parent")

            q_p = await session.execute(
                select(VendorModel).where(
                    and_(
                        VendorModel.tenant_id == self.tenant_id,
                        VendorModel.id == parent_id,
                    )
                )
            )
            parent = q_p.scalar_one_or_none()
            if not parent:
                raise ValueError(f"Parent vendor {parent_id} not found")
            if parent.status == "merged":
                raise ValueError(f"Cannot set parent to merged vendor {parent_id}")

        q_c = await session.execute(
            select(VendorModel).where(
                and_(
                    VendorModel.tenant_id == self.tenant_id,
                    VendorModel.id == child_id,
                )
            )
        )
        child = q_c.scalar_one_or_none()
        if not child:
            raise ValueError(f"Child vendor {child_id} not found")

        # Hierarchy lock
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"{self.tenant_id}:hierarchy"},
        )

        child.parent_vendor_id = parent_id
        session.add(child)
        await session.flush()
        return child
