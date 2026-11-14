"""Transactional Outbox Worker for Neo4j projection synchronization."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from procure_context.graph.repository import GraphRepository
from procure_context.models import ApprovalNode, ContractNode, VendorNode
from procure_core.models.vendor import Neo4jOutboxModel

logger = logging.getLogger(__name__)


class OutboxWorker:
    """Consumes the PostgreSQL outbox and reliably projects vendor events into Neo4j."""

    def __init__(
        self,
        tenant_id: str | None = None,
        graph_repo_factory: Any | None = None,
    ) -> None:
        self.tenant_id = tenant_id
        self._graph_repo_factory = graph_repo_factory or (
            lambda tid: GraphRepository(tenant_id=tid)
        )

    def _get_repo(self, tenant_id: str) -> GraphRepository:
        return self._graph_repo_factory(tenant_id)

    async def dispatch_event(self, event: Neo4jOutboxModel) -> None:
        """Route event payload to appropriate GraphRepository projection method."""
        repo = self._get_repo(event.tenant_id)
        p = event.payload
        event_type = event.event_type

        if event_type == "vendor_created":
            parent_id = uuid.UUID(p["parent_id"]) if p.get("parent_id") else None
            node = VendorNode(
                id=uuid.UUID(p["id"]),
                tenant_id=event.tenant_id,
                name=p["name"],
                canonical_key=p["canonical_key"],
                normalized_base=p["normalized_base"],
                region=p.get("region"),
                status=p.get("status", "active"),
                projection_version=event.aggregate_version,
            )
            await repo.upsert_vendor_node(node, parent_id=parent_id)

        elif event_type == "vendor_linked":
            parent_id = uuid.UUID(p["parent_id"])
            child_id = uuid.UUID(p["child_id"])
            await repo.create_subsidiary_edge(parent_id, child_id)

        elif event_type == "vendor_merged":
            source_id = uuid.UUID(p["source_id"])
            target_id = uuid.UUID(p["target_id"])
            await repo.merge_vendor_nodes(
                source_id, target_id, projection_version=event.aggregate_version
            )

        elif event_type == "vendor_unmerged":
            source_id = uuid.UUID(p["source_id"])
            target_id = uuid.UUID(p["target_id"])
            await repo.unmerge_vendor_nodes(
                source_id, target_id, projection_version=event.aggregate_version
            )

        elif event_type == "contract_created":
            contract = ContractNode(
                id=uuid.UUID(p["id"]),
                tenant_id=event.tenant_id,
                vendor_id=uuid.UUID(p["vendor_id"]),
                title=p["title"],
                status=p["status"],
                total_value=Decimal(str(p["total_value"])),
                currency=p["currency"],
                start_date=p["start_date"],
                end_date=p.get("end_date"),
            )
            await repo.link_contract(contract)

        elif event_type == "approval_created":
            approval = ApprovalNode(
                id=uuid.UUID(p["id"]),
                tenant_id=event.tenant_id,
                request_id=uuid.UUID(p["request_id"]),
                vendor_id=uuid.UUID(p["vendor_id"]) if p.get("vendor_id") else None,
                approver_id=p["approver_id"],
                status=p["status"],
                step=p.get("step", 1),
            )
            await repo.link_approval(approval)

        else:
            logger.warning("Unrecognized outbox event type: %s", event_type)

    async def process_batch(self, session: AsyncSession, limit: int = 50) -> int:
        """Process a batch of outbox events preserving ordering and dead-letter blocking."""
        # 1. Identify ordering keys that are currently dead-lettered
        failed_q = select(Neo4jOutboxModel.ordering_key).where(
            Neo4jOutboxModel.status == "dead_letter"
        )
        if self.tenant_id:
            failed_q = failed_q.where(Neo4jOutboxModel.tenant_id == self.tenant_id)
        failed_res = await session.execute(failed_q.distinct())
        blocked_keys = set(failed_res.scalars().all())

        # 2. Query pending events using FOR UPDATE SKIP LOCKED
        now_utc = datetime.now(UTC)
        query = (
            select(Neo4jOutboxModel)
            .where(
                and_(
                    Neo4jOutboxModel.status == "pending",
                    Neo4jOutboxModel.next_attempt_at <= now_utc,
                )
            )
            .order_by(Neo4jOutboxModel.sequence.asc())
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        if self.tenant_id:
            query = query.where(Neo4jOutboxModel.tenant_id == self.tenant_id)

        rows = (await session.execute(query)).scalars().all()
        if not rows:
            return 0

        processed_count = 0
        current_blocked_keys = set(blocked_keys)

        for event in rows:
            # If this aggregate's ordering key is blocked by a previous failure, skip it
            if event.ordering_key in current_blocked_keys:
                logger.debug(
                    "Skipping event %s because ordering_key %s is blocked",
                    event.id,
                    event.ordering_key,
                )
                continue

            try:
                await self.dispatch_event(event)
                event.status = "processed"
                event.processed_at = datetime.now(UTC)
                event.last_error = None
                processed_count += 1
            except Exception as exc:
                event.attempts += 1
                event.last_error = str(exc)
                logger.error(
                    "Outbox event %s failed (attempt %d): %s", event.id, event.attempts, exc
                )

                if event.attempts >= event.max_attempts:
                    event.status = "dead_letter"
                    current_blocked_keys.add(event.ordering_key)
                    logger.critical(
                        "Event %s entered DEAD-LETTER status; blocking ordering_key %s",
                        event.id,
                        event.ordering_key,
                    )
                else:
                    event.status = "pending"
                    backoff_sec = 2 ** min(event.attempts, 6)
                    event.next_attempt_at = datetime.now(UTC) + timedelta(seconds=backoff_sec)
                    current_blocked_keys.add(event.ordering_key)

            session.add(event)

        await session.flush()
        return processed_count

    async def replay_events(
        self,
        session: AsyncSession,
        tenant_id: str,
        aggregate_id: str | None = None,
        include_processed: bool = False,
        actor_role: str = "admin",
        actor: str = "ops-admin@omnea.ai",
    ) -> int:
        """Reset dead-letter or processed events to pending for projection replay (admin-only)."""
        if actor_role != "admin":
            raise PermissionError("Admin role required to trigger outbox event replay")

        logger.info(
            "AUDIT: Outbox event replay executed by actor '%s' (role: %s) for tenant '%s' "
            "(aggregate_id=%s, include_processed=%s)",
            actor,
            actor_role,
            tenant_id,
            aggregate_id,
            include_processed,
        )

        target_statuses = ["dead_letter"]
        if include_processed:
            target_statuses.append("processed")

        cond = and_(
            Neo4jOutboxModel.tenant_id == tenant_id,
            Neo4jOutboxModel.status.in_(target_statuses),
        )
        if aggregate_id:
            cond = and_(cond, Neo4jOutboxModel.aggregate_id == aggregate_id)

        stmt = (
            update(Neo4jOutboxModel)
            .where(cond)
            .values(
                status="pending",
                attempts=0,
                next_attempt_at=func.now(),
                last_error=None,
                processed_at=None,
            )
        )
        res = await session.execute(stmt)
        await session.flush()
        return int(getattr(res, "rowcount", 0) or 0)
