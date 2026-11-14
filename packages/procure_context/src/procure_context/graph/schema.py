"""Neo4j schema constraints and indexes for enterprise context graph."""

from __future__ import annotations

import logging

from procure_context.graph.client import execute_cypher_write

logger = logging.getLogger(__name__)

SCHEMA_STATEMENTS: list[str] = [
    # 1. Uniqueness constraints on (tenant_id, id)
    """
    CREATE CONSTRAINT vendor_tenant_id_unique IF NOT EXISTS
    FOR (v:Vendor) REQUIRE (v.tenant_id, v.id) IS UNIQUE;
    """,
    """
    CREATE CONSTRAINT contract_tenant_id_unique IF NOT EXISTS
    FOR (c:Contract) REQUIRE (c.tenant_id, c.id) IS UNIQUE;
    """,
    """
    CREATE CONSTRAINT approval_tenant_id_unique IF NOT EXISTS
    FOR (a:Approval) REQUIRE (a.tenant_id, a.id) IS UNIQUE;
    """,
    """
    CREATE CONSTRAINT department_tenant_id_unique IF NOT EXISTS
    FOR (d:Department) REQUIRE (d.tenant_id, d.id) IS UNIQUE;
    """,
    """
    CREATE CONSTRAINT document_tenant_id_unique IF NOT EXISTS
    FOR (doc:Document) REQUIRE (doc.tenant_id, doc.id) IS UNIQUE;
    """,
    # 2. Performance indexes
    """
    CREATE INDEX vendor_canonical_key IF NOT EXISTS
    FOR (v:Vendor) ON (v.tenant_id, v.canonical_key);
    """,
    """
    CREATE INDEX vendor_status IF NOT EXISTS
    FOR (v:Vendor) ON (v.tenant_id, v.status);
    """,
]


async def init_neo4j_schema() -> None:
    """Initialize all constraints and indexes in Neo4j."""
    for stmt in SCHEMA_STATEMENTS:
        trimmed = stmt.strip()
        if trimmed:
            logger.info("Applying Neo4j schema: %s", trimmed.split("\n")[0])
            await execute_cypher_write(trimmed)
