"""Repository layer for Neo4j context graph operations."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from procure_context.graph.client import execute_cypher_read, execute_cypher_write
from procure_context.models import ApprovalNode, ContractNode, VendorNode

logger = logging.getLogger(__name__)


class GraphRepository:
    """Provides strongly-typed, tenant-isolated operations on the Neo4j knowledge graph."""

    def __init__(self, tenant_id: str) -> None:
        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string for GraphRepository operations")
        self.tenant_id = tenant_id

    async def upsert_vendor_node(
        self,
        node: VendorNode,
        parent_id: uuid.UUID | None = None,
    ) -> None:
        """Upsert a Vendor node respecting projection version, and optionally link parent."""
        cypher = """
        MERGE (v:Vendor {tenant_id: $tenant_id, id: $id})
        ON CREATE SET
            v.name = $name,
            v.canonical_key = $canonical_key,
            v.normalized_base = $normalized_base,
            v.region = $region,
            v.status = $status,
            v.projection_version = $projection_version,
            v.created_at = datetime()
        ON MATCH SET
            v.name = CASE WHEN $projection_version >= coalesce(v.projection_version, 0)
                          THEN $name ELSE v.name END,
            v.canonical_key = CASE WHEN $projection_version >= coalesce(v.projection_version, 0)
                                   THEN $canonical_key ELSE v.canonical_key END,
            v.normalized_base = CASE WHEN $projection_version >= coalesce(v.projection_version, 0)
                                     THEN $normalized_base ELSE v.normalized_base END,
            v.region = CASE WHEN $projection_version >= coalesce(v.projection_version, 0)
                            THEN $region ELSE v.region END,
            v.status = CASE WHEN $projection_version >= coalesce(v.projection_version, 0)
                            THEN $status ELSE v.status END,
            v.projection_version = CASE
                WHEN $projection_version >= coalesce(v.projection_version, 0)
                THEN $projection_version ELSE v.projection_version END,
            v.updated_at = datetime();
        """
        params: dict[str, Any] = {
            "tenant_id": self.tenant_id,
            "id": str(node.id),
            "name": node.name,
            "canonical_key": node.canonical_key,
            "normalized_base": node.normalized_base,
            "region": node.region,
            "status": node.status,
            "projection_version": node.projection_version,
        }
        await execute_cypher_write(cypher, params)

        if parent_id is not None:
            await self.create_subsidiary_edge(parent_id, node.id)

    async def create_subsidiary_edge(
        self,
        parent_id: uuid.UUID,
        child_id: uuid.UUID,
    ) -> None:
        """Create SUBSIDIARY_OF relationship from child to parent vendor (reverse is derived)."""
        cypher = """
        MATCH (p:Vendor {tenant_id: $tenant_id, id: $parent_id})
        MATCH (c:Vendor {tenant_id: $tenant_id, id: $child_id})
        MERGE (c)-[:SUBSIDIARY_OF]->(p)
        SET c.parent_vendor_id = $parent_id;
        """
        params = {
            "tenant_id": self.tenant_id,
            "parent_id": str(parent_id),
            "child_id": str(child_id),
        }
        await execute_cypher_write(cypher, params)

    async def merge_vendor_nodes(
        self,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
        projection_version: int = 1,
    ) -> None:
        """Project vendor merge: mark source as merged and create MERGED_INTO relationship."""
        cypher = """
        MATCH (s:Vendor {tenant_id: $tenant_id, id: $source_id})
        WHERE $projection_version >= coalesce(s.projection_version, 0)
        MERGE (t:Vendor {tenant_id: $tenant_id, id: $target_id})
        SET s.previous_status = s.status,
            s.status = 'merged',
            s.merged_into_id = $target_id,
            s.projection_version = $projection_version,
            s.updated_at = datetime()
        MERGE (s)-[:MERGED_INTO]->(t);
        """
        params = {
            "tenant_id": self.tenant_id,
            "source_id": str(source_id),
            "target_id": str(target_id),
            "projection_version": projection_version,
        }
        await execute_cypher_write(cypher, params)

    async def unmerge_vendor_nodes(
        self,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
        projection_version: int = 1,
    ) -> None:
        """Project vendor unmerge: remove MERGED_INTO edge and restore original status."""
        cypher = """
        MATCH (s:Vendor {tenant_id: $tenant_id, id: $source_id})
              -[r:MERGED_INTO]->(t:Vendor {tenant_id: $tenant_id, id: $target_id})
        WHERE $projection_version >= coalesce(s.projection_version, 0)
        DELETE r
        SET s.status = coalesce(s.previous_status, 'active'),
            s.previous_status = null,
            s.merged_into_id = null,
            s.projection_version = $projection_version,
            s.updated_at = datetime();
        """
        params = {
            "tenant_id": self.tenant_id,
            "source_id": str(source_id),
            "target_id": str(target_id),
            "projection_version": projection_version,
        }
        await execute_cypher_write(cypher, params)

    async def get_vendor_hierarchy(
        self,
        vendor_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Fetch vendor hierarchy with bounded traversal (*1..4) including merged pointers."""
        # Check if the requested vendor is merged and follow the pointer
        pointer_cypher = """
        MATCH (v:Vendor {tenant_id: $tenant_id, id: $vendor_id})
              -[:MERGED_INTO*1..4]->(active:Vendor)
        RETURN active.id AS active_id, active.name AS active_name
        LIMIT 1;
        """
        pointer_res = await execute_cypher_read(
            pointer_cypher, {"tenant_id": self.tenant_id, "vendor_id": str(vendor_id)}
        )
        effective_id = pointer_res[0]["active_id"] if pointer_res else str(vendor_id)

        cypher = """
        MATCH (v:Vendor {tenant_id: $tenant_id, id: $vendor_id})
        OPTIONAL MATCH (v)-[:SUBSIDIARY_OF*1..4]->(ancestor:Vendor)
        OPTIONAL MATCH (descendant:Vendor)-[:SUBSIDIARY_OF*1..4]->(v)
        OPTIONAL MATCH (merged:Vendor)-[:MERGED_INTO]->(v)
        RETURN
            v.id AS id,
            v.name AS name,
            v.canonical_key AS canonical_key,
            v.status AS status,
            v.parent_vendor_id AS direct_parent_id,
            collect(DISTINCT ancestor.id) AS ancestor_ids,
            collect(DISTINCT descendant.id) AS descendant_ids,
            collect(DISTINCT merged.id) AS merged_into_this_ids;
        """
        results = await execute_cypher_read(
            cypher, {"tenant_id": self.tenant_id, "vendor_id": effective_id}
        )
        if not results:
            return {
                "vendor_id": uuid.UUID(effective_id),
                "active_survivor_id": uuid.UUID(effective_id),
                "parent_id": None,
                "ancestor_ids": [],
                "subsidiary_ids": [],
                "merged_vendor_ids": [],
            }

        row = results[0]
        ancestors = [uuid.UUID(aid) for aid in row.get("ancestor_ids", []) if aid]
        descendants = [uuid.UUID(did) for did in row.get("descendant_ids", []) if did]
        merged_ids = [uuid.UUID(mid) for mid in row.get("merged_into_this_ids", []) if mid]
        parent_id = uuid.UUID(row["direct_parent_id"]) if row.get("direct_parent_id") else None

        return {
            "vendor_id": uuid.UUID(effective_id),
            "requested_vendor_id": vendor_id,
            "was_merged": effective_id != str(vendor_id),
            "name": row.get("name"),
            "status": row.get("status"),
            "parent_id": parent_id,
            "ancestor_ids": ancestors,
            "subsidiary_ids": descendants,
            "merged_vendor_ids": merged_ids,
        }

    async def link_contract(self, contract: ContractNode) -> None:
        """Upsert a Contract node, connect to Vendor, and link supersedes chain."""
        cypher = """
        MATCH (v:Vendor {tenant_id: $tenant_id, id: $vendor_id})
        MERGE (c:Contract {tenant_id: $tenant_id, id: $id})
        ON CREATE SET
            c.title = $title,
            c.status = $status,
            c.total_value = $total_value,
            c.currency = $currency,
            c.start_date = $start_date,
            c.end_date = $end_date,
            c.created_at = datetime()
        ON MATCH SET
            c.title = $title,
            c.status = $status,
            c.total_value = $total_value,
            c.currency = $currency,
            c.start_date = $start_date,
            c.end_date = $end_date,
            c.updated_at = datetime()
        MERGE (v)-[:HAS_CONTRACT]->(c);
        """
        params = {
            "tenant_id": self.tenant_id,
            "id": str(contract.id),
            "vendor_id": str(contract.vendor_id),
            "title": contract.title,
            "status": contract.status,
            "total_value": float(contract.total_value),
            "currency": contract.currency,
            "start_date": str(contract.start_date),
            "end_date": str(contract.end_date) if contract.end_date else None,
        }
        await execute_cypher_write(cypher, params)

    async def link_approval(self, approval: ApprovalNode) -> None:
        """Upsert an Approval node and connect to Vendor."""
        if approval.vendor_id is None:
            return
        cypher = """
        MATCH (v:Vendor {tenant_id: $tenant_id, id: $vendor_id})
        MERGE (a:Approval {tenant_id: $tenant_id, id: $id})
        ON CREATE SET
            a.request_id = $request_id,
            a.approver_id = $approver_id,
            a.status = $status,
            a.step = $step,
            a.created_at = datetime()
        MERGE (v)-[:APPROVED_BY]->(a);
        """
        params = {
            "tenant_id": self.tenant_id,
            "id": str(approval.id),
            "vendor_id": str(approval.vendor_id),
            "request_id": str(approval.request_id),
            "approver_id": approval.approver_id,
            "status": approval.status,
            "step": approval.step,
        }
        await execute_cypher_write(cypher, params)

    async def reconcile_check(
        self,
        postgres_vendor_ids: list[uuid.UUID],
    ) -> dict[str, list[uuid.UUID]]:
        """Compare Postgres vendor identity ledger against Neo4j Vendor node projection."""
        cypher = """
        MATCH (v:Vendor {tenant_id: $tenant_id})
        RETURN v.id AS id;
        """
        records = await execute_cypher_read(cypher, {"tenant_id": self.tenant_id})
        neo4j_ids = {uuid.UUID(r["id"]) for r in records if r.get("id")}
        pg_ids = set(postgres_vendor_ids)

        missing_in_neo4j = list(pg_ids - neo4j_ids)
        extra_in_neo4j = list(neo4j_ids - pg_ids)

        return {
            "missing_in_neo4j": missing_in_neo4j,
            "extra_in_neo4j": extra_in_neo4j,
        }
