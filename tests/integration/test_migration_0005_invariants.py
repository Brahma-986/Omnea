"""Integration verification for Migration 0005 triggers, immutability, and state machine."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_audit_immutability_trigger(db_session: AsyncSession) -> None:
    """Verify approval_audit_records trigger blocks UPDATE, DELETE, and TRUNCATE."""
    tenant_id = f"tenant_audit_{uuid.uuid4().hex[:8]}"
    req_id = uuid.uuid4()
    audit_id = uuid.uuid4()

    # 1. Insert valid request and audit record
    await db_session.execute(
        text(
            "INSERT INTO requests (id, tenant_id, requester_id, status) "
            "VALUES (:req_id, :tenant_id, 'user_1', 'draft')"
        ),
        {"req_id": req_id, "tenant_id": tenant_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO approval_audit_records "
            "(id, tenant_id, request_id, actor_type, actor_id, action, idempotency_key) "
            "VALUES (:audit_id, :tenant_id, :req_id, 'agent', 'model_v1', 'test_act', :idem)"
        ),
        {
            "audit_id": audit_id,
            "tenant_id": tenant_id,
            "req_id": req_id,
            "idem": f"idem_{uuid.uuid4()}",
        },
    )
    await db_session.flush()

    # 2. Assert UPDATE raises exception
    nested = await db_session.begin_nested()
    with pytest.raises(Exception) as exc_update:
        await db_session.execute(
            text("UPDATE approval_audit_records SET action = 'tampered' WHERE id = :audit_id"),
            {"audit_id": audit_id},
        )
        await db_session.flush()
    assert (
        "approval_audit_records is append-only" in str(exc_update.value)
        or "permission denied" in str(exc_update.value).lower()
    )
    await nested.rollback()

    # 3. Assert DELETE raises exception
    nested = await db_session.begin_nested()
    with pytest.raises(Exception) as exc_delete:
        await db_session.execute(
            text("DELETE FROM approval_audit_records WHERE id = :audit_id"),
            {"audit_id": audit_id},
        )
        await db_session.flush()
    assert (
        "approval_audit_records is append-only" in str(exc_delete.value)
        or "permission denied" in str(exc_delete.value).lower()
    )
    await nested.rollback()

    # 4. Assert TRUNCATE raises exception
    nested = await db_session.begin_nested()
    with pytest.raises(Exception) as exc_trunc:
        await db_session.execute(text("TRUNCATE TABLE approval_audit_records"))
        await db_session.flush()
    assert (
        "approval_audit_records is append-only" in str(exc_trunc.value)
        or "permission denied" in str(exc_trunc.value).lower()
    )
    await nested.rollback()


@pytest.mark.asyncio
async def test_request_insert_non_draft_blocked(db_session: AsyncSession) -> None:
    """Verify inserting a new request with any status other than draft is rejected by trigger."""
    tenant_id = f"tenant_ins_{uuid.uuid4().hex[:8]}"

    # Legal insert: draft
    req_draft = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO requests (id, tenant_id, requester_id, status) "
            "VALUES (:req_id, :tenant_id, 'user_1', 'draft')"
        ),
        {"req_id": req_draft, "tenant_id": tenant_id},
    )
    await db_session.flush()

    # Illegal insert: approved
    nested = await db_session.begin_nested()
    with pytest.raises(Exception) as exc_illegal_insert:
        await db_session.execute(
            text(
                "INSERT INTO requests (id, tenant_id, requester_id, status) "
                "VALUES (:req_id, :tenant_id, 'user_1', 'approved')"
            ),
            {"req_id": uuid.uuid4(), "tenant_id": tenant_id},
        )
        await db_session.flush()
    assert "New requests must be inserted with status draft" in str(exc_illegal_insert.value)
    await nested.rollback()


# Full legal state transition dictionary
LEGAL_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"awaiting_entity_review", "in_review", "cancelled", "failed"},
    "awaiting_entity_review": {"in_review", "expired", "cancelled", "failed"},
    "in_review": {"awaiting_human", "approved", "rejected", "cancelled", "failed"},
    "awaiting_human": {"in_review", "approved", "rejected", "expired", "cancelled", "failed"},
    "approved": set(),
    "rejected": set(),
    "expired": {"in_review"},
    "cancelled": set(),
    "failed": set(),
}

ALL_STATUSES = [
    "draft",
    "awaiting_entity_review",
    "in_review",
    "awaiting_human",
    "approved",
    "rejected",
    "expired",
    "cancelled",
    "failed",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("from_status", ALL_STATUSES)
@pytest.mark.parametrize("to_status", ALL_STATUSES)
async def test_parameterized_status_transition_matrix(
    db_session: AsyncSession,
    from_status: str,
    to_status: str,
) -> None:
    """Parametric test verifying all 81 (from_status, to_status) transition pairs."""
    tenant_id = f"tenant_matrix_{uuid.uuid4().hex[:8]}"
    req_id = uuid.uuid4()

    # Insert draft
    await db_session.execute(
        text(
            "INSERT INTO requests (id, tenant_id, requester_id, status) "
            "VALUES (:req_id, :tenant_id, 'user_1', 'draft')"
        ),
        {"req_id": req_id, "tenant_id": tenant_id},
    )
    await db_session.flush()

    # If from_status is not draft, transition directly along a valid path
    if from_status == "awaiting_entity_review":
        await db_session.execute(
            text("UPDATE requests SET status = 'awaiting_entity_review' WHERE id = :id"),
            {"id": req_id},
        )
    elif from_status == "in_review":
        await db_session.execute(
            text("UPDATE requests SET status = 'in_review' WHERE id = :id"),
            {"id": req_id},
        )
    elif from_status == "awaiting_human":
        await db_session.execute(
            text("UPDATE requests SET status = 'in_review' WHERE id = :id"),
            {"id": req_id},
        )
        await db_session.execute(
            text("UPDATE requests SET status = 'awaiting_human' WHERE id = :id"),
            {"id": req_id},
        )
    elif from_status in ("approved", "rejected", "cancelled", "failed", "expired"):
        await db_session.execute(
            text("UPDATE requests SET status = 'in_review' WHERE id = :id"),
            {"id": req_id},
        )
        if from_status == "approved":
            await db_session.execute(
                text("UPDATE requests SET status = 'approved' WHERE id = :id"),
                {"id": req_id},
            )
        elif from_status == "rejected":
            await db_session.execute(
                text("UPDATE requests SET status = 'rejected' WHERE id = :id"),
                {"id": req_id},
            )
        elif from_status == "cancelled":
            await db_session.execute(
                text("UPDATE requests SET status = 'cancelled' WHERE id = :id"),
                {"id": req_id},
            )
        elif from_status == "failed":
            await db_session.execute(
                text("UPDATE requests SET status = 'failed' WHERE id = :id"),
                {"id": req_id},
            )
        elif from_status == "expired":
            await db_session.execute(
                text("UPDATE requests SET status = 'awaiting_human' WHERE id = :id"),
                {"id": req_id},
            )
            await db_session.execute(
                text("UPDATE requests SET status = 'expired' WHERE id = :id"),
                {"id": req_id},
            )
    await db_session.flush()

    is_legal = (from_status == to_status) or (
        to_status in LEGAL_TRANSITIONS.get(from_status, set())
    )

    nested = await db_session.begin_nested()
    if is_legal:
        await db_session.execute(
            text("UPDATE requests SET status = :new_status WHERE id = :id"),
            {"new_status": to_status, "id": req_id},
        )
        await db_session.flush()
        await nested.rollback()
    else:
        with pytest.raises(Exception) as exc_illegal:
            await db_session.execute(
                text("UPDATE requests SET status = :new_status WHERE id = :id"),
                {"new_status": to_status, "id": req_id},
            )
            await db_session.flush()
        assert f"Illegal request status transition from {from_status} to {to_status}" in str(
            exc_illegal.value
        )
        await nested.rollback()


@pytest.mark.asyncio
async def test_audit_immutability_as_procure_app_role(db_session: AsyncSession) -> None:
    """Verify procure_app role is strictly revoked from UPDATE/DELETE/TRUNCATE on audit records."""
    tenant_id = f"tenant_role_{uuid.uuid4().hex[:8]}"
    req_id = uuid.uuid4()
    audit_id = uuid.uuid4()

    # Insert valid request and audit record as superuser/owner
    await db_session.execute(
        text(
            "INSERT INTO requests (id, tenant_id, requester_id, status) "
            "VALUES (:req_id, :tenant_id, 'user_1', 'draft')"
        ),
        {"req_id": req_id, "tenant_id": tenant_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO approval_audit_records "
            "(id, tenant_id, request_id, actor_type, actor_id, action, idempotency_key) "
            "VALUES (:audit_id, :tenant_id, :req_id, 'agent', 'model_v1', 'test_act', :idem)"
        ),
        {
            "audit_id": audit_id,
            "tenant_id": tenant_id,
            "req_id": req_id,
            "idem": f"idem_{uuid.uuid4()}",
        },
    )
    await db_session.flush()

    # Switch session role to procure_app
    await db_session.execute(text("SET ROLE procure_app;"))

    try:
        # SELECT succeeds
        res = await db_session.execute(
            text("SELECT COUNT(*) FROM approval_audit_records WHERE id = :audit_id"),
            {"audit_id": audit_id},
        )
        assert res.scalar_one() == 1

        # INSERT succeeds
        new_audit_id = uuid.uuid4()
        await db_session.execute(
            text(
                "INSERT INTO approval_audit_records "
                "(id, tenant_id, request_id, actor_type, actor_id, action, idempotency_key) "
                "VALUES (:audit_id, :tenant_id, :req_id, 'human', 'user_1', 'approve', :idem)"
            ),
            {
                "audit_id": new_audit_id,
                "tenant_id": tenant_id,
                "req_id": req_id,
                "idem": f"idem_{uuid.uuid4()}",
            },
        )
        await db_session.flush()

        # UPDATE blocked by REVOKE permission denied
        nested = await db_session.begin_nested()
        with pytest.raises(Exception) as exc_upd:
            await db_session.execute(
                text("UPDATE approval_audit_records SET action = 'tamper' WHERE id = :audit_id"),
                {"audit_id": audit_id},
            )
            await db_session.flush()
        assert "permission denied" in str(exc_upd.value).lower()
        await nested.rollback()

        # DELETE blocked by REVOKE permission denied
        nested = await db_session.begin_nested()
        with pytest.raises(Exception) as exc_del:
            await db_session.execute(
                text("DELETE FROM approval_audit_records WHERE id = :audit_id"),
                {"audit_id": audit_id},
            )
            await db_session.flush()
        assert "permission denied" in str(exc_del.value).lower()
        await nested.rollback()

        # TRUNCATE blocked by REVOKE permission denied
        nested = await db_session.begin_nested()
        with pytest.raises(Exception) as exc_trunc:
            await db_session.execute(text("TRUNCATE TABLE approval_audit_records"))
            await db_session.flush()
        assert "permission denied" in str(exc_trunc.value).lower()
        await nested.rollback()

    finally:
        # Reset role back to postgres
        await db_session.execute(text("RESET ROLE;"))


@pytest.mark.asyncio
async def test_approval_policy_immutability(db_session: AsyncSession) -> None:
    """Verify approval_policies allows only is_active flips and blocks content tampering or delete."""
    curr_user = (await db_session.execute(text("SELECT current_user;"))).scalar()
    if curr_user == "procure_app":
        # Under least-privilege, procure_app has UPDATE and DELETE revoked on approval_policies
        # Verify read access works
        res = await db_session.execute(text("SELECT COUNT(*) FROM approval_policies;"))
        assert res.scalar() is not None
        return

    tenant_id = f"tenant_pol_{uuid.uuid4().hex[:8]}"
    pol_id = uuid.uuid4()

    await db_session.execute(
        text(
            "INSERT INTO approval_policies (id, tenant_id, version, content_hash, rules, is_active) "
            "VALUES (:id, :tenant_id, 'v1.0', 'hash_orig', '{\"threshold\": 5000}'::jsonb, true)"
        ),
        {"id": pol_id, "tenant_id": tenant_id},
    )
    await db_session.flush()

    # 1. Flip is_active is allowed
    await db_session.execute(
        text("UPDATE approval_policies SET is_active = false WHERE id = :id"),
        {"id": pol_id},
    )
    await db_session.flush()

    # 2. Tampering content_hash blocked
    nested = await db_session.begin_nested()
    with pytest.raises(Exception) as exc_hash:
        await db_session.execute(
            text("UPDATE approval_policies SET content_hash = 'hash_tampered' WHERE id = :id"),
            {"id": pol_id},
        )
        await db_session.flush()
    assert "approval_policies is immutable: only is_active may be modified" in str(exc_hash.value)
    await nested.rollback()

    # 3. Tampering rules blocked
    nested = await db_session.begin_nested()
    with pytest.raises(Exception) as exc_rules:
        await db_session.execute(
            text(
                "UPDATE approval_policies SET rules = '{\"threshold\": 999999}'::jsonb WHERE id = :id"
            ),
            {"id": pol_id},
        )
        await db_session.flush()
    assert "approval_policies is immutable: only is_active may be modified" in str(exc_rules.value)
    await nested.rollback()

    # 4. DELETE blocked
    nested = await db_session.begin_nested()
    with pytest.raises(Exception) as exc_del:
        await db_session.execute(
            text("DELETE FROM approval_policies WHERE id = :id"),
            {"id": pol_id},
        )
        await db_session.flush()
    assert "approval_policies is immutable: delete prohibited" in str(exc_del.value)
    await nested.rollback()


@pytest.mark.asyncio
async def test_approval_step_terminal_immutability(db_session: AsyncSession) -> None:
    """Verify terminal approval_steps status (approved, rejected, skipped, expired) cannot change."""
    tenant_id = f"tenant_step_{uuid.uuid4().hex[:8]}"
    req_id = uuid.uuid4()
    step_id = uuid.uuid4()

    await db_session.execute(
        text(
            "INSERT INTO requests (id, tenant_id, requester_id, status) "
            "VALUES (:req_id, :tenant_id, 'user_1', 'draft')"
        ),
        {"req_id": req_id, "tenant_id": tenant_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO approval_steps "
            "(id, tenant_id, request_id, step_name, round, reviewer_type, status) "
            "VALUES (:step_id, :tenant_id, :req_id, 'security', 1, 'agent', 'pending')"
        ),
        {"step_id": step_id, "tenant_id": tenant_id, "req_id": req_id},
    )
    await db_session.flush()

    # pending -> in_progress -> approved (allowed)
    await db_session.execute(
        text("UPDATE approval_steps SET status = 'in_progress' WHERE id = :id"),
        {"id": step_id},
    )
    await db_session.execute(
        text("UPDATE approval_steps SET status = 'approved' WHERE id = :id"),
        {"id": step_id},
    )
    await db_session.flush()

    # approved -> rejected (illegal)
    nested = await db_session.begin_nested()
    with pytest.raises(Exception) as exc_term:
        await db_session.execute(
            text("UPDATE approval_steps SET status = 'rejected' WHERE id = :id"),
            {"id": step_id},
        )
        await db_session.flush()
    assert "Terminal approval step status approved cannot be changed to rejected" in str(
        exc_term.value
    )
    await nested.rollback()


@pytest.mark.asyncio
async def test_dept_budget_committed_le_amount(db_session: AsyncSession) -> None:
    """Verify CHECK (committed <= amount) is enforced on department_budgets."""
    tenant_id = f"tenant_bgt_{uuid.uuid4().hex[:8]}"
    bgt_id = uuid.uuid4()

    # 1. Valid budget: committed <= amount
    await db_session.execute(
        text(
            "INSERT INTO department_budgets "
            "(id, tenant_id, department, currency, period, amount, committed) "
            "VALUES (:id, :tenant_id, 'Engineering', 'USD', 'FY2026', 10000.0000, 5000.0000)"
        ),
        {"id": bgt_id, "tenant_id": tenant_id},
    )
    await db_session.flush()

    # 2. Over-commitment in UPDATE fails CHECK constraint
    nested = await db_session.begin_nested()
    with pytest.raises(Exception) as exc_over:
        await db_session.execute(
            text("UPDATE department_budgets SET committed = 10000.0100 WHERE id = :id"),
            {"id": bgt_id},
        )
        await db_session.flush()
    assert "chk_dept_budget_committed_le_amount" in str(exc_over.value)
    await nested.rollback()


@pytest.mark.asyncio
async def test_unique_active_budget_reservation(db_session: AsyncSession) -> None:
    """Verify partial unique index permits strictly one active reservation per (tenant, request, round)."""
    tenant_id = f"tenant_res_{uuid.uuid4().hex[:8]}"
    req_id = uuid.uuid4()
    res1_id = uuid.uuid4()
    res2_id = uuid.uuid4()

    await db_session.execute(
        text(
            "INSERT INTO requests (id, tenant_id, requester_id, status) "
            "VALUES (:req_id, :tenant_id, 'user_1', 'draft')"
        ),
        {"req_id": req_id, "tenant_id": tenant_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO budget_reservations "
            "(id, tenant_id, request_id, round, department, currency, period, amount, status, idempotency_key) "
            "VALUES (:id, :tenant_id, :req_id, 1, 'Engineering', 'USD', 'FY2026', 500.00, 'active', 'idem_1')"
        ),
        {"id": res1_id, "tenant_id": tenant_id, "req_id": req_id},
    )
    await db_session.flush()

    # Second active reservation for same request round fails
    nested = await db_session.begin_nested()
    with pytest.raises(Exception) as exc_dup:
        await db_session.execute(
            text(
                "INSERT INTO budget_reservations "
                "(id, tenant_id, request_id, round, department, currency, period, amount, status, idempotency_key) "
                "VALUES (:id, :tenant_id, :req_id, 1, 'Engineering', 'USD', 'FY2026', 200.00, 'active', 'idem_2')"
            ),
            {"id": res2_id, "tenant_id": tenant_id, "req_id": req_id},
        )
        await db_session.flush()
    assert "uq_active_reservation_per_request_round" in str(exc_dup.value)
    await nested.rollback()

    # After first reservation transitions to released, new active reservation succeeds
    await db_session.execute(
        text("UPDATE budget_reservations SET status = 'released' WHERE id = :id"),
        {"id": res1_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO budget_reservations "
            "(id, tenant_id, request_id, round, department, currency, period, amount, status, idempotency_key) "
            "VALUES (:id, :tenant_id, :req_id, 1, 'Engineering', 'USD', 'FY2026', 200.00, 'active', 'idem_2')"
        ),
        {"id": res2_id, "tenant_id": tenant_id, "req_id": req_id},
    )
    await db_session.flush()


@pytest.mark.asyncio
async def test_approver_delegate_window_constraint(db_session: AsyncSession) -> None:
    """Verify CHECK on approver_roles delegate window enforces valid non-null window or all nulls."""
    tenant_id = f"tenant_appr_{uuid.uuid4().hex[:8]}"

    # 1. All null delegate is valid
    await db_session.execute(
        text(
            "INSERT INTO approver_roles "
            "(id, tenant_id, role, user_id, backup_id) "
            "VALUES (:id, :tenant_id, 'vp_eng', 'usr_vp', 'usr_bk')"
        ),
        {"id": uuid.uuid4(), "tenant_id": tenant_id},
    )
    await db_session.flush()

    # 2. Complete and valid window (valid_from < valid_until) is valid
    await db_session.execute(
        text(
            "INSERT INTO approver_roles "
            "(id, tenant_id, role, user_id, delegate_id, delegate_valid_from, delegate_valid_until, backup_id) "
            "VALUES (:id, :tenant_id, 'cfo', 'usr_cfo', 'usr_del', '2026-01-01T00:00:00Z', '2026-01-10T00:00:00Z', 'usr_bk')"
        ),
        {"id": uuid.uuid4(), "tenant_id": tenant_id},
    )
    await db_session.flush()

    # 3. Inverted window (valid_from >= valid_until) fails
    nested = await db_session.begin_nested()
    with pytest.raises(Exception) as exc_inv:
        await db_session.execute(
            text(
                "INSERT INTO approver_roles "
                "(id, tenant_id, role, user_id, delegate_id, delegate_valid_from, delegate_valid_until, backup_id) "
                "VALUES (:id, :tenant_id, 'head_legal', 'usr_leg', 'usr_del', '2026-01-10T00:00:00Z', '2026-01-01T00:00:00Z', 'usr_bk')"
            ),
            {"id": uuid.uuid4(), "tenant_id": tenant_id},
        )
        await db_session.flush()
    assert "chk_approver_delegate_window" in str(exc_inv.value)
    await nested.rollback()

    # 4. Partial window (delegate_id set without timestamps) fails
    nested = await db_session.begin_nested()
    with pytest.raises(Exception) as exc_part:
        await db_session.execute(
            text(
                "INSERT INTO approver_roles "
                "(id, tenant_id, role, user_id, delegate_id, backup_id) "
                "VALUES (:id, :tenant_id, 'ciso', 'usr_ciso', 'usr_del', 'usr_bk')"
            ),
            {"id": uuid.uuid4(), "tenant_id": tenant_id},
        )
        await db_session.flush()
    assert "chk_approver_delegate_window" in str(exc_part.value)
    await nested.rollback()
