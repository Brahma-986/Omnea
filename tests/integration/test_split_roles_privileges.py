"""Verification tests for split DB roles: procure_workflow, procure_reviewer, procure_ingest, procure_admin."""

from __future__ import annotations

import os
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

TEST_DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
TEST_DB_PORT = os.getenv("POSTGRES_PORT", "5432")
TEST_DB_NAME = os.getenv("POSTGRES_DB", "procure_os")
PROCURE_PASS = os.getenv("PROCURE_APP_PASSWORD", "procure_app_pass")


@pytest.mark.asyncio
async def test_procure_workflow_least_privilege_guards() -> None:
    """Verify procure_workflow cannot INSERT approver_roles, cannot UPDATE budget.amount, cannot DELETE outbox."""
    url = f"postgresql+asyncpg://procure_workflow:{PROCURE_PASS}@{TEST_DB_HOST}:{TEST_DB_PORT}/{TEST_DB_NAME}"
    engine = create_async_engine(url)

    async def check_stmt_blocked(sql: str, params: dict | None = None) -> None:
        async with engine.connect() as c:
            with pytest.raises(Exception) as exc:
                await c.execute(text(sql), params or {})
            assert "permission denied" in str(exc.value).lower(), f"Expected permission denied for: {sql}, got: {exc.value}"

    # 1. SELECT on context tables works
    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT COUNT(*) FROM approver_roles;"))
        assert res.scalar() is not None

    tenant_id = f"t_wf_{uuid.uuid4().hex[:6]}"

    # 2. INSERT into approver_roles is blocked
    await check_stmt_blocked(
        "INSERT INTO approver_roles (id, tenant_id, role, user_id, backup_id) VALUES (:id, :tenant_id, 'vp_eng', 'usr_1', 'usr_2')",
        {"id": uuid.uuid4(), "tenant_id": tenant_id},
    )

    # 3. INSERT into user_identities is blocked
    await check_stmt_blocked(
        "INSERT INTO user_identities (id, tenant_id, internal_user_id, email) VALUES (:id, :tenant_id, 'usr_1', 'alice@omnea.internal')",
        {"id": uuid.uuid4(), "tenant_id": tenant_id},
    )

    # 4. INSERT into contracts is blocked
    await check_stmt_blocked(
        "INSERT INTO contracts (id, tenant_id, vendor_id, title, total_value, currency, start_date, end_date) "
        "VALUES (:id, :tenant_id, :v_id, 'MSA', 1000, 'USD', '2026-01-01', '2027-01-01')",
        {"id": uuid.uuid4(), "tenant_id": tenant_id, "v_id": uuid.uuid4()},
    )

    # 5. INSERT into vendor_security_reviews is blocked
    await check_stmt_blocked(
        "INSERT INTO vendor_security_reviews (id, tenant_id, vendor_id, status) VALUES (:id, :tenant_id, :v_id, 'compliant')",
        {"id": uuid.uuid4(), "tenant_id": tenant_id, "v_id": uuid.uuid4()},
    )

    # 6. UPDATE on department_budgets.amount is blocked (column-level restriction)
    await check_stmt_blocked(
        "UPDATE department_budgets SET amount = 9999999.0 WHERE tenant_id = :tenant_id",
        {"tenant_id": tenant_id},
    )

    # 7. UPDATE on vendors is blocked
    await check_stmt_blocked("UPDATE vendors SET name = 'Hacked' WHERE id = '00000000-0000-0000-0000-000000000000';")

    # 8. UPDATE on requests.requester_id is blocked (only status and updated_at permitted)
    await check_stmt_blocked(
        "UPDATE requests SET requester_id = 'attacker' WHERE tenant_id = :tenant_id",
        {"tenant_id": tenant_id},
    )

    # 9. DELETE on neo4j_outbox is blocked
    await check_stmt_blocked("DELETE FROM neo4j_outbox;")

    # 10. DELETE on entity_review_tasks is blocked
    await check_stmt_blocked("DELETE FROM entity_review_tasks;")

    await engine.dispose()


@pytest.mark.asyncio
async def test_procure_reviewer_is_strictly_read_only() -> None:
    """Verify procure_reviewer has zero write permissions and cannot read approver_roles/user_identities."""
    url = f"postgresql+asyncpg://procure_reviewer:{PROCURE_PASS}@{TEST_DB_HOST}:{TEST_DB_PORT}/{TEST_DB_NAME}"
    engine = create_async_engine(url)

    async def check_reviewer_blocked(sql: str, params: dict | None = None) -> None:
        async with engine.connect() as c:
            with pytest.raises(Exception) as exc:
                await c.execute(text(sql), params or {})
            assert "permission denied" in str(exc.value).lower(), f"Expected permission denied for: {sql}, got: {exc.value}"

    # SELECT on requests works
    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT COUNT(*) FROM requests;"))
        assert res.scalar() is not None

    # Any INSERT fails
    await check_reviewer_blocked(
        "INSERT INTO requests (id, tenant_id, requester_id, status) VALUES (:id, 't1', 'u1', 'draft')",
        {"id": uuid.uuid4()},
    )

    # SELECT on approver_roles is BLOCKED (dropped reviewer read)
    await check_reviewer_blocked("SELECT * FROM approver_roles;")

    # SELECT on user_identities is BLOCKED (dropped reviewer read)
    await check_reviewer_blocked("SELECT * FROM user_identities;")

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_cannot_update_or_delete_audit_table() -> None:
    """Verify approval_audit_records is append-only even for procure_admin role."""
    url = f"postgresql+asyncpg://procure_admin:{PROCURE_PASS}@{TEST_DB_HOST}:{TEST_DB_PORT}/{TEST_DB_NAME}"
    engine = create_async_engine(url)

    async def check_admin_blocked(sql: str) -> None:
        async with engine.connect() as c:
            with pytest.raises(Exception) as exc:
                await c.execute(text(sql))
            msg = str(exc.value).lower()
            assert "permission denied" in msg or "append-only" in msg, f"Expected append-only/permission denied: {msg}"

    # UPDATE is blocked
    await check_admin_blocked("UPDATE approval_audit_records SET action = 'tampered';")

    # DELETE is blocked
    await check_admin_blocked("DELETE FROM approval_audit_records;")

    # TRUNCATE on approval_audit_records is blocked
    await check_admin_blocked("TRUNCATE TABLE approval_audit_records;")

    # TRUNCATE on audit_log is blocked
    await check_admin_blocked("TRUNCATE TABLE audit_log;")

    # UPDATE on audit_log is blocked
    await check_admin_blocked("UPDATE audit_log SET actor = 'tampered';")

    # DELETE on audit_log is blocked
    await check_admin_blocked("DELETE FROM audit_log;")

    await engine.dispose()
