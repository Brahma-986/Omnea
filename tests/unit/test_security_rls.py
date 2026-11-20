"""Unit tests for core security, RLS guards, RBAC engine, and audit hash chain."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from procure_core.auth.rbac import RbacEngine
from procure_core.db import get_tenant_session
from procure_core.rls.graph_guard import (
    TenantGraphViolationError,
    validate_cypher_tenant_isolation,
)
from procure_core.rls.storage_guard import (
    TenantStorageViolationError,
    validate_s3_tenant_prefix,
)
from procure_core.rls.vector_guard import (
    TenantVectorViolationError,
    validate_vector_query,
)
from procure_core.security.audit_chain import AuditHashChainManager


# =============================================================================
# 1. Storage Guard Tests
# =============================================================================
@pytest.mark.unit
def test_storage_guard_valid_key() -> None:
    """Valid storage key with matching tenant prefix succeeds."""
    tenant = "tenant_alpha"
    key = "tenant_alpha/2026/11/doc-1234.pdf"
    validate_s3_tenant_prefix(tenant, key)


@pytest.mark.unit
def test_storage_guard_missing_params() -> None:
    """Missing key or tenant_id raises TenantStorageViolationError."""
    with pytest.raises(TenantStorageViolationError, match="Missing tenant_id or key"):
        validate_s3_tenant_prefix("", "tenant_1/file.pdf")

    with pytest.raises(TenantStorageViolationError, match="Missing tenant_id or key"):
        validate_s3_tenant_prefix("tenant_1", "")


@pytest.mark.unit
def test_storage_guard_boundary_violation() -> None:
    """Key not starting with tenant prefix raises TenantStorageViolationError."""
    with pytest.raises(TenantStorageViolationError, match="violates tenant boundary"):
        validate_s3_tenant_prefix("tenant_1", "tenant_2/file.pdf")

    with pytest.raises(TenantStorageViolationError, match="violates tenant boundary"):
        validate_s3_tenant_prefix("tenant_1", "tenant_1_extra/file.pdf")


@pytest.mark.unit
def test_storage_guard_path_traversal() -> None:
    """Path traversal indicators '..' and '//' raise TenantStorageViolationError."""
    with pytest.raises(TenantStorageViolationError, match="Path traversal detected"):
        validate_s3_tenant_prefix("tenant_1", "tenant_1/../etc/passwd")

    with pytest.raises(TenantStorageViolationError, match="Path traversal detected"):
        validate_s3_tenant_prefix("tenant_1", "tenant_1//hidden/file.pdf")


# =============================================================================
# 2. Graph Guard Tests
# =============================================================================
@pytest.mark.unit
def test_graph_guard_valid_cypher() -> None:
    """Cypher query referencing tenant_id with matching expected param succeeds."""
    query = "MATCH (v:Vendor {tenant_id: $tenant_id}) RETURN v;"
    params = {"tenant_id": "tenant_xyz"}
    validate_cypher_tenant_isolation(query, params, expected_tenant_id="tenant_xyz")


@pytest.mark.unit
def test_graph_guard_missing_query_tenant_reference() -> None:
    """Cypher query missing tenant_id reference raises TenantGraphViolationError."""
    query = "MATCH (v:Vendor) RETURN v;"
    with pytest.raises(TenantGraphViolationError, match="Query does not reference tenant_id"):
        validate_cypher_tenant_isolation(query, {"tenant_id": "tenant_xyz"})


@pytest.mark.unit
def test_graph_guard_cross_tenant_violation() -> None:
    """Mismatch between expected_tenant_id and params raises TenantGraphViolationError."""
    query = "MATCH (n {tenant_id: $tenant_id}) RETURN n;"
    params = {"tenant_id": "tenant_attacker"}
    with pytest.raises(TenantGraphViolationError, match="Cross-tenant Cypher query violation"):
        validate_cypher_tenant_isolation(query, params, expected_tenant_id="tenant_victim")

    with pytest.raises(TenantGraphViolationError, match="Cross-tenant Cypher query violation"):
        validate_cypher_tenant_isolation(query, {}, expected_tenant_id="tenant_victim")


# =============================================================================
# 3. Vector Guard Tests
# =============================================================================
@pytest.mark.unit
def test_vector_guard_valid_query() -> None:
    """SQL with tenant_id predicate and matching params succeeds."""
    sql = (
        "SELECT id FROM vendor_embeddings "
        "WHERE tenant_id = :tenant_id "
        "ORDER BY embedding <-> :vec LIMIT 5;"
    )
    params = {"tenant_id": "t1", "vec": [0.1, 0.2]}
    validate_vector_query(sql, params, expected_tenant_id="t1")


@pytest.mark.unit
def test_vector_guard_missing_sql_predicate() -> None:
    """SQL missing tenant_id filter predicate raises TenantVectorViolationError."""
    sql = "SELECT id FROM vendor_embeddings ORDER BY embedding <-> :vec LIMIT 5;"
    with pytest.raises(TenantVectorViolationError, match="lacks tenant_id filter predicate"):
        validate_vector_query(sql, {"tenant_id": "t1"})


@pytest.mark.unit
def test_vector_guard_cross_tenant_violation() -> None:
    """Mismatch in expected vs actual tenant_id parameter raises TenantVectorViolationError."""
    sql = "SELECT * FROM vendor_embeddings WHERE tenant_id = :tenant_id;"
    with pytest.raises(TenantVectorViolationError, match="Cross-tenant vector query violation"):
        validate_vector_query(sql, {"tenant_id": "t_bad"}, expected_tenant_id="t_good")

    with pytest.raises(TenantVectorViolationError, match="Cross-tenant vector query violation"):
        validate_vector_query(sql, {}, expected_tenant_id="t_good")


# =============================================================================
# 4. RBAC Engine Unit Tests (Mocked DB session)
# =============================================================================
@pytest.mark.unit
async def test_rbac_engine_empty_arguments() -> None:
    """Empty tenant, user, or permission code returns False."""
    session = AsyncMock(spec=AsyncSession)
    assert not await RbacEngine.has_permission(session, "", "usr1", "request:read")
    assert not await RbacEngine.has_permission(session, "t1", "", "request:read")
    assert not await RbacEngine.has_permission(session, "t1", "usr1", "")


@pytest.mark.unit
async def test_rbac_engine_fallback_roles() -> None:
    """Fallback role assignment grants configured permissions."""
    session = AsyncMock(spec=AsyncSession)
    mock_res = MagicMock()
    mock_res.mappings.return_value = []
    session.execute.return_value = mock_res
    session.begin_nested.side_effect = Exception("No DB tables")

    tenant = f"t_fallback_{uuid.uuid4().hex[:6]}"
    user = f"u_fallback_{uuid.uuid4().hex[:6]}"

    RbacEngine.clear_fallback_roles()
    assert not await RbacEngine.has_permission(session, tenant, user, "request:approve")

    # Register CFO role
    RbacEngine.register_fallback_role(tenant, user, "cfo")
    assert await RbacEngine.has_permission(session, tenant, user, "request:approve")
    assert await RbacEngine.has_permission(session, tenant, user, "budget:approve")
    assert not await RbacEngine.has_permission(session, tenant, user, "unrelated:perm")

    # Register admin role
    RbacEngine.register_fallback_role(tenant, user, "admin")
    assert await RbacEngine.has_permission(session, tenant, user, "unrelated:perm")

    RbacEngine.clear_fallback_roles()


# =============================================================================
# 5. Integration Tests: RBAC, Audit Chain, and get_tenant_session
# =============================================================================
@pytest.mark.integration
async def test_get_tenant_session_validation_and_variable() -> None:
    """get_tenant_session validates tenant_id and sets session config app.tenant_id."""
    with pytest.raises(ValueError, match="tenant_id is strictly required"):
        async with get_tenant_session(""):
            pass

    with pytest.raises(ValueError, match="tenant_id is strictly required"):
        async with get_tenant_session("   "):
            pass

    test_tenant = f"t_sess_{uuid.uuid4().hex[:8]}"
    async with get_tenant_session(test_tenant) as session:
        res = await session.execute(text("SELECT current_setting('app.tenant_id', true);"))
        assert res.scalar() == test_tenant


@pytest.mark.integration
async def test_rbac_engine_database_roles(db_session: AsyncSession) -> None:
    """RbacEngine correctly evaluates roles from user_identities and approver_roles."""
    tenant = f"t_rbac_{uuid.uuid4().hex[:8]}"
    internal_user = f"usr_int_{uuid.uuid4().hex[:6]}"
    email = f"lead_{uuid.uuid4().hex[:4]}@company.internal"
    delegate_user = f"usr_del_{uuid.uuid4().hex[:6]}"

    # 1. Insert identity linking email to internal_user
    await db_session.execute(
        text(
            """
            INSERT INTO user_identities (
                id, tenant_id, provider, external_user_id, internal_user_id, email
            ) VALUES (
                :id, :tenant_id, 'okta', :ext_id, :int_id, :email
            );
            """
        ),
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant,
            "ext_id": f"ext_{uuid.uuid4().hex[:6]}",
            "int_id": internal_user,
            "email": email,
        },
    )

    # 2. Insert approver role for internal_user as 'vp_eng' with an active delegate
    now = datetime.now(UTC)
    await db_session.execute(
        text(
            """
            INSERT INTO approver_roles (
                id, tenant_id, role, user_id, delegate_id,
                delegate_valid_from, delegate_valid_until, backup_id
            ) VALUES (
                :id, :tenant_id, 'vp_eng', :user_id, :delegate_id,
                :val_from, :val_until, :backup_id
            );
            """
        ),
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant,
            "user_id": internal_user,
            "delegate_id": delegate_user,
            "val_from": now - timedelta(days=1),
            "val_until": now + timedelta(days=1),
            "backup_id": f"bk_{uuid.uuid4().hex[:6]}",
        },
    )
    await db_session.commit()

    # Query using email -> resolves identity -> evaluates vp_eng -> has request:approve
    has_perm = await RbacEngine.has_permission(db_session, tenant, email, "request:approve")
    assert has_perm is True

    # Active delegate also has request:approve
    has_del_perm = await RbacEngine.has_permission(
        db_session, tenant, delegate_user, "request:approve"
    )
    assert has_del_perm is True

    # Unknown user does not have permission
    has_unknown = await RbacEngine.has_permission(
        db_session, tenant, "unknown_user", "request:approve"
    )
    assert has_unknown is False


@pytest.mark.integration
async def test_audit_hash_chain_append_and_verify(db_session: AsyncSession) -> None:
    """AuditHashChainManager creates table, appends chained hashes, and verifies tampering."""
    tenant = f"t_audit_{uuid.uuid4().hex[:8]}"

    # 1. Empty chain is valid
    res_empty = await AuditHashChainManager.verify_chain(db_session, tenant)
    assert res_empty.is_valid is True
    assert res_empty.total_entries == 0
    assert res_empty.tampered_sequence is None

    # 2. Append first entry
    h1 = await AuditHashChainManager.append_entry(
        db_session,
        tenant_id=tenant,
        record_type="contract",
        record_id="c_101",
        actor_id="actor_alice",
        action="contract_created",
        payload={"value": 10000, "currency": "USD"},
    )
    assert len(h1) == 64

    # 3. Append second entry
    h2 = await AuditHashChainManager.append_entry(
        db_session,
        tenant_id=tenant,
        record_type="contract",
        record_id="c_101",
        actor_id="actor_bob",
        action="contract_approved",
        payload={"approved": True},
    )
    assert len(h2) == 64
    assert h1 != h2

    # 4. Append third entry
    h3 = await AuditHashChainManager.append_entry(
        db_session,
        tenant_id=tenant,
        record_type="vendor",
        record_id="v_202",
        actor_id="actor_charlie",
        action="vendor_onboarded",
        payload={"category": "saas"},
    )
    assert len(h3) == 64

    # 5. Verify untampered chain
    verify_res = await AuditHashChainManager.verify_chain(db_session, tenant)
    assert verify_res.is_valid is True
    assert verify_res.total_entries == 3
    assert verify_res.tampered_sequence is None

    # 6. Insert a fourth entry with a corrupted hash to simulate tampering
    await db_session.execute(
        text(
            """
            INSERT INTO audit_hash_chain (
                tenant_id, sequence_number, record_type, record_id,
                actor_id, action, payload, prev_hash, entry_hash
            ) VALUES (
                :tenant, 4, 'vendor', 'v_202',
                'actor_charlie', 'vendor_modified', '{}'::jsonb,
                :prev_hash, :bad_hash
            );
            """
        ),
        {"tenant": tenant, "prev_hash": h3, "bad_hash": "f" * 64},
    )
    await db_session.flush()

    # 7. Verification detects tampering at sequence 4
    tampered_res = await AuditHashChainManager.verify_chain(db_session, tenant)
    assert tampered_res.is_valid is False
    assert tampered_res.total_entries == 4
    assert tampered_res.tampered_sequence == 4
