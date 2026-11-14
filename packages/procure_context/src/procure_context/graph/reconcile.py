"""Graph Reconciliation Service to audit PostgreSQL ledger against Neo4j projection."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from procure_context.graph.client import execute_cypher_read
from procure_core.models.vendor import VendorModel

logger = logging.getLogger(__name__)


class ReconcileReport(BaseModel):
    """Audit report comparing Postgres identity ledger and Neo4j graph nodes."""

    tenant_id: str
    postgres_vendor_count: int
    neo4j_vendor_count: int
    missing_in_neo4j: list[uuid.UUID] = Field(default_factory=list)
    extra_in_neo4j: list[uuid.UUID] = Field(default_factory=list)
    status_mismatches: list[dict[str, Any]] = Field(default_factory=list)
    is_consistent: bool = True


class ReconcileService:
    """Audits consistency between PostgreSQL vendors table and Neo4j vendor graph."""

    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id

    async def reconcile(self, session: AsyncSession) -> ReconcileReport:
        # 1. Fetch all vendors from Postgres
        q_pg = await session.execute(
            select(VendorModel).where(VendorModel.tenant_id == self.tenant_id)
        )
        pg_vendors = {v.id: v for v in q_pg.scalars().all()}

        # 2. Fetch all vendors from Neo4j
        cypher = """
        MATCH (v:Vendor {tenant_id: $tenant_id})
        RETURN v.id AS id, v.name AS name, v.status AS status, v.canonical_key AS canonical_key;
        """
        neo_rows = await execute_cypher_read(cypher, {"tenant_id": self.tenant_id})
        neo_vendors: dict[uuid.UUID, dict[str, Any]] = {}
        for r in neo_rows:
            try:
                vid = uuid.UUID(r["id"])
                neo_vendors[vid] = r
            except Exception:
                pass

        pg_ids = set(pg_vendors.keys())
        neo_ids = set(neo_vendors.keys())

        missing_in_neo4j = sorted(pg_ids - neo_ids)
        extra_in_neo4j = sorted(neo_ids - pg_ids)

        status_mismatches: list[dict[str, Any]] = []
        for common_id in pg_ids.intersection(neo_ids):
            pg_v = pg_vendors[common_id]
            neo_v = neo_vendors[common_id]
            if pg_v.status != neo_v.get("status"):
                status_mismatches.append(
                    {
                        "vendor_id": str(common_id),
                        "postgres_status": pg_v.status,
                        "neo4j_status": neo_v.get("status"),
                    }
                )

        is_consistent = (
            len(missing_in_neo4j) == 0 and len(extra_in_neo4j) == 0 and len(status_mismatches) == 0
        )

        return ReconcileReport(
            tenant_id=self.tenant_id,
            postgres_vendor_count=len(pg_vendors),
            neo4j_vendor_count=len(neo_vendors),
            missing_in_neo4j=missing_in_neo4j,
            extra_in_neo4j=extra_in_neo4j,
            status_mismatches=status_mismatches,
            is_consistent=is_consistent,
        )
