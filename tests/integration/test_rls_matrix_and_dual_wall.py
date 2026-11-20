"""Integration tests for PostgreSQL Row-Level Security (RLS) Dual-Wall enforcement.

Dual-Wall Invariants:
1. Fail-Closed: Unscoped session (app.tenant_id unset) returns 0 rows.
2. Dual-Wall Wall 1 (RLS): Omitted tenant_id predicate is blocked by RLS.
3. Dual-Wall Wall 2 (App predicate): Bypassed RLS is blocked by app query predicate.
4. INSERT check: Inserting a row with tenant_id != app.tenant_id raises RLS violation.
5. Storage prefix check: S3 client enforces {tenant_id}/ prefix and blocks traversal.
6. Cypher graph guard: Enforces tenant_id on graph queries.
7. Vector guard: Enforces tenant_id on pgvector searches.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from procure_core.rls.graph_guard import (
    TenantGraphViolationError,
    validate_cypher_tenant_isolation,
)
from procure_core.rls.storage_guard import (
    TenantStorageViolationError,
    validate_s3_tenant_prefix,
)
from procure_core.rls.vector_guard import TenantVectorViolationError, validate_vector_query

TEST_DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
TEST_DB_PORT = os.getenv("POSTGRES_PORT", "5432")
TEST_DB_NAME = os.getenv("POSTGRES_DB", "procure_os")
WORKFLOW_PASS = os.getenv("PROCURE_WORKFLOW_PASSWORD") or os.getenv(
    "PROCURE_APP_PASSWORD", "procure_app_pass"
)


@pytest.fixture
def workflow_engine() -> Iterator[AsyncEngine]:
    url = (
        f"postgresql+asyncpg://procure_workflow:{WORKFLOW_PASS}@"
        f"{TEST_DB_HOST}:{TEST_DB_PORT}/{TEST_DB_NAME}"
    )
    engine = create_async_engine(url, poolclass=NullPool)
    yield engine


@pytest.mark.asyncio
async def test_rls_fail_closed_unscoped_session_returns_zero_rows(
    db_session: AsyncSession,
    workflow_engine: AsyncEngine,
) -> None:
    """Proves: A session without app.tenant_id set returns 0 rows on tenant tables (fail-closed)."""
    tenant_a = f"t_a_{uuid.uuid4().hex[:6]}"
    req_id = uuid.uuid4()

    # Seed row as superuser
    await db_session.execute(
        text(
            "INSERT INTO requests (id, tenant_id, requester_id, status) "
            "VALUES (:id, :t, 'alice', 'draft')"
        ),
        {"id": req_id, "t": tenant_a},
    )
    await db_session.commit()

    # Query as procure_workflow WITHOUT setting app.tenant_id
    async with workflow_engine.connect() as conn:
        res = await conn.execute(
            text("SELECT id FROM requests WHERE id = :id"),
            {"id": req_id},
        )
        rows = res.fetchall()
        # MUST return 0 rows because RLS filters out all rows when app.tenant_id is NULL
        assert len(rows) == 0


@pytest.mark.asyncio
async def test_dual_wall_1_rls_blocks_when_app_predicate_omitted(
    db_session: AsyncSession,
    workflow_engine: AsyncEngine,
) -> None:
    """Proves: Dual-Wall 1 - Omitted WHERE tenant_id is restricted to bound tenant by RLS."""
    tenant_a = f"t_a_{uuid.uuid4().hex[:6]}"
    tenant_b = f"t_b_{uuid.uuid4().hex[:6]}"
    req_a = uuid.uuid4()
    req_b = uuid.uuid4()

    # Seed rows for both tenants
    await db_session.execute(
        text(
            "INSERT INTO requests (id, tenant_id, requester_id, status) "
            "VALUES (:id, :t, 'alice', 'draft')"
        ),
        {"id": req_a, "t": tenant_a},
    )
    await db_session.execute(
        text(
            "INSERT INTO requests (id, tenant_id, requester_id, status) "
            "VALUES (:id, :t, 'bob', 'draft')"
        ),
        {"id": req_b, "t": tenant_b},
    )
    await db_session.commit()

    # Session bound to tenant_a runs query WITHOUT tenant predicate (SELECT * FROM requests)
    async with workflow_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"),
            {"t": tenant_a},
        )
        res = await conn.execute(
            text("SELECT id, tenant_id FROM requests WHERE id IN (:ra, :rb)"),
            {"ra": req_a, "rb": req_b},
        )
        found = res.fetchall()
        assert len(found) == 1
        assert found[0][0] == req_a
        assert found[0][1] == tenant_a


@pytest.mark.asyncio
async def test_dual_wall_2_rls_disabled_app_predicate_blocks(
    db_session: AsyncSession,
) -> None:
    """Proves: Dual-Wall 2 - With RLS disabled, app predicate WHERE tenant_id = :t blocks access."""
    tenant_a = f"t_a_{uuid.uuid4().hex[:6]}"
    tenant_b = f"t_b_{uuid.uuid4().hex[:6]}"
    req_a = uuid.uuid4()
    req_b = uuid.uuid4()

    await db_session.execute(
        text(
            "INSERT INTO requests (id, tenant_id, requester_id, status) "
            "VALUES (:id, :t, 'alice', 'draft')"
        ),
        {"id": req_a, "t": tenant_a},
    )
    await db_session.execute(
        text(
            "INSERT INTO requests (id, tenant_id, requester_id, status) "
            "VALUES (:id, :t, 'bob', 'draft')"
        ),
        {"id": req_b, "t": tenant_b},
    )
    await db_session.commit()

    try:
        # Explicitly disable RLS to prove application predicate works independently
        await db_session.execute(text("ALTER TABLE requests DISABLE ROW LEVEL SECURITY;"))
        await db_session.commit()

        # Application query contains WHERE tenant_id = :t
        res = await db_session.execute(
            text("SELECT id FROM requests WHERE tenant_id = :t"),
            {"t": tenant_a},
        )
        ids = [r[0] for r in res.fetchall()]
        # Tenant A's row is returned; Tenant B's row is strictly excluded by application predicate
        assert req_a in ids
        assert req_b not in ids
        assert len(ids) == 1
    finally:
        # Restore RLS and FORCE RLS
        await db_session.execute(text("ALTER TABLE requests ENABLE ROW LEVEL SECURITY;"))
        await db_session.execute(text("ALTER TABLE requests FORCE ROW LEVEL SECURITY;"))
        await db_session.commit()


@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_insert(workflow_engine: AsyncEngine) -> None:
    """Proves: Inserting row with tenant_id != current app.tenant_id raises RLS check error."""
    tenant_a = f"t_a_{uuid.uuid4().hex[:6]}"
    tenant_b = f"t_b_{uuid.uuid4().hex[:6]}"

    async with workflow_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"),
            {"t": tenant_a},
        )
        with pytest.raises(Exception) as exc:
            await conn.execute(
                text(
                    "INSERT INTO requests (id, tenant_id, requester_id, status) "
                    "VALUES (:id, :t, 'attacker', 'draft')"
                ),
                {"id": uuid.uuid4(), "t": tenant_b},
            )
        assert (
            "row-level security" in str(exc.value).lower()
            or "insufficientprivilege" in str(exc.value).lower()
        )


def test_s3_tenant_prefix_validation() -> None:
    """Proves: Storage path validator enforces tenant prefix and rejects path traversal."""
    tenant = "tenant_123"

    # Valid key
    validate_s3_tenant_prefix(tenant, "tenant_123/documents/soc2.pdf")

    # Wrong prefix
    with pytest.raises(TenantStorageViolationError, match="violates tenant boundary"):
        validate_s3_tenant_prefix(tenant, "other_tenant/documents/soc2.pdf")

    # Path traversal
    with pytest.raises(TenantStorageViolationError, match="Path traversal detected"):
        validate_s3_tenant_prefix(tenant, "tenant_123/../secrets/keys.env")

    with pytest.raises(TenantStorageViolationError, match="Path traversal detected"):
        validate_s3_tenant_prefix(tenant, "tenant_123/foo//bar")


def test_cypher_tenant_isolation_validation() -> None:
    """Proves: Graph Cypher guard requires tenant_id predicate and matching params."""
    valid_query = "MATCH (v:Vendor {tenant_id: $tenant_id}) RETURN v"
    validate_cypher_tenant_isolation(
        valid_query, {"tenant_id": "tenant_xyz"}, expected_tenant_id="tenant_xyz"
    )

    # Missing tenant_id in query
    with pytest.raises(TenantGraphViolationError, match="does not reference tenant_id"):
        validate_cypher_tenant_isolation("MATCH (v:Vendor) RETURN v", {"tenant_id": "tenant_xyz"})

    # Mismatched tenant_id in params
    with pytest.raises(TenantGraphViolationError, match="Cross-tenant Cypher query violation"):
        validate_cypher_tenant_isolation(
            valid_query, {"tenant_id": "tenant_abc"}, expected_tenant_id="tenant_xyz"
        )


def test_pgvector_tenant_isolation_validation() -> None:
    """Proves: pgvector guard requires tenant_id filtering on similarity search."""
    valid_sql = (
        "SELECT id FROM document_chunks WHERE tenant_id = :tenant_id "
        "ORDER BY embedding <-> :vec LIMIT 5"
    )
    validate_vector_query(valid_sql, {"tenant_id": "t1"}, expected_tenant_id="t1")

    # Missing tenant_id predicate in similarity query
    with pytest.raises(TenantVectorViolationError, match="lacks tenant_id filter predicate"):
        validate_vector_query(
            "SELECT id FROM document_chunks ORDER BY embedding <-> :vec LIMIT 5",
            {"tenant_id": "t1"},
        )


@pytest.mark.asyncio
async def test_production_session_factory_binds_tenant_id() -> None:
    """Proves: Production get_tenant_session correctly executes SET LOCAL app.tenant_id."""
    from procure_core.db import get_tenant_session

    tenant_id = f"tenant_{uuid.uuid4().hex[:6]}"
    async with get_tenant_session(tenant_id) as session:
        res = await session.execute(text("SELECT current_setting('app.tenant_id', true);"))
        val = res.scalar()
        assert val == tenant_id, f"Expected app.tenant_id to be '{tenant_id}', got '{val}'"
