"""Integration test verifying end-to-end request resubmission after T3 expiry."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_resubmission_lifecycle_reuses_request_row(db_session: AsyncSession) -> None:
    """Verify request row reuse, expired->in_review transition, reservation release and re-acquisition."""
    tenant_id = f"tenant_resub_{uuid.uuid4().hex[:8]}"
    req_id = uuid.uuid4()
    user_id = "requester_bob"
    dept = "Engineering"
    curr = "USD"
    period = "FY2026"

    # 1. Setup Department Budget: $50,000 budget, $0 committed
    bgt_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO department_budgets "
            "(id, tenant_id, department, currency, period, amount, committed) "
            "VALUES (:id, :tenant_id, :dept, :curr, :period, 50000.0000, 0.0000)"
        ),
        {
            "id": bgt_id,
            "tenant_id": tenant_id,
            "dept": dept,
            "curr": curr,
            "period": period,
        },
    )

    # 2. Insert draft request (Round 1)
    await db_session.execute(
        text(
            "INSERT INTO requests (id, tenant_id, requester_id, status) "
            "VALUES (:id, :tenant_id, :user_id, 'draft')"
        ),
        {"id": req_id, "tenant_id": tenant_id, "user_id": user_id},
    )
    await db_session.flush()

    # 3. Transition: draft -> in_review
    await db_session.execute(
        text("UPDATE requests SET status = 'in_review' WHERE id = :id"),
        {"id": req_id},
    )
    await db_session.flush()

    # 4. Acquire Round 1 budget reservation ($12,000 active)
    res_r1_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO budget_reservations "
            "(id, tenant_id, request_id, round, department, currency, period, amount, status, idempotency_key) "
            "VALUES (:id, :tenant_id, :req_id, 1, :dept, :curr, :period, 12000.0000, 'active', :idem)"
        ),
        {
            "id": res_r1_id,
            "tenant_id": tenant_id,
            "req_id": req_id,
            "dept": dept,
            "curr": curr,
            "period": period,
            "idem": f"idem_res_{uuid.uuid4()}",
        },
    )
    await db_session.flush()

    # Insert Round 1 approval steps
    step_r1_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO approval_steps "
            "(id, tenant_id, request_id, step_name, round, reviewer_type, status) "
            "VALUES (:id, :tenant_id, :req_id, 'finance', 1, 'agent', 'approved')"
        ),
        {"id": step_r1_id, "tenant_id": tenant_id, "req_id": req_id},
    )
    await db_session.flush()

    # 5. Escalate to human: in_review -> awaiting_human
    await db_session.execute(
        text("UPDATE requests SET status = 'awaiting_human' WHERE id = :id"),
        {"id": req_id},
    )
    await db_session.flush()

    # 6. T3 Timer fires without human action -> awaiting_human -> expired
    await db_session.execute(
        text("UPDATE requests SET status = 'expired' WHERE id = :id"),
        {"id": req_id},
    )
    await db_session.flush()

    # 7. Workflow cleanup activity releases Round 1 reservation on expiry
    await db_session.execute(
        text(
            "UPDATE budget_reservations "
            "SET status = 'released', released_at = clock_timestamp() "
            "WHERE id = :id AND status = 'active'"
        ),
        {"id": res_r1_id},
    )
    await db_session.flush()

    # Assert Round 1 state
    res_r1_status = await db_session.scalar(
        text("SELECT status FROM budget_reservations WHERE id = :id"),
        {"id": res_r1_id},
    )
    assert res_r1_status == "released"

    req_status_expired = await db_session.scalar(
        text("SELECT status FROM requests WHERE id = :id"),
        {"id": req_id},
    )
    assert req_status_expired == "expired"

    # =========================================================================
    # Resubmission (Round 2)
    # =========================================================================
    # 8. User resubmits: Transition expired -> in_review reusing the SAME request row
    await db_session.execute(
        text("UPDATE requests SET status = 'in_review' WHERE id = :id"),
        {"id": req_id},
    )
    await db_session.flush()

    # 9. New round starts: Round 2 workflow ID = f"{tenant_id}:{req_id}:r2"
    wf_id_r2 = f"{tenant_id}:{req_id}:r2"
    assert wf_id_r2.endswith(":r2")

    # 10. New approval step created for round 2
    step_r2_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO approval_steps "
            "(id, tenant_id, request_id, step_name, round, reviewer_type, status) "
            "VALUES (:id, :tenant_id, :req_id, 'finance', 2, 'agent', 'pending')"
        ),
        {"id": step_r2_id, "tenant_id": tenant_id, "req_id": req_id},
    )
    await db_session.flush()

    # 11. Fresh budget reservation acquired for Round 2 ($12,000 active)
    res_r2_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO budget_reservations "
            "(id, tenant_id, request_id, round, department, currency, period, amount, status, idempotency_key) "
            "VALUES (:id, :tenant_id, :req_id, 2, :dept, :curr, :period, 12000.0000, 'active', :idem)"
        ),
        {
            "id": res_r2_id,
            "tenant_id": tenant_id,
            "req_id": req_id,
            "dept": dept,
            "curr": curr,
            "period": period,
            "idem": f"idem_res_{uuid.uuid4()}",
        },
    )
    await db_session.flush()

    # Verify both reservations in database: r1 is released, r2 is active
    rows = (
        await db_session.execute(
            text(
                "SELECT round, status, amount FROM budget_reservations "
                "WHERE tenant_id = :tenant_id AND request_id = :req_id ORDER BY round"
            ),
            {"tenant_id": tenant_id, "req_id": req_id},
        )
    ).fetchall()

    assert len(rows) == 2
    assert rows[0][0] == 1 and rows[0][1] == "released"
    assert rows[1][0] == 2 and rows[1][1] == "active"

    # Verify only ONE active reservation exists for this request
    active_count = await db_session.scalar(
        text(
            "SELECT COUNT(*) FROM budget_reservations "
            "WHERE tenant_id = :tenant_id AND request_id = :req_id AND status = 'active'"
        ),
        {"tenant_id": tenant_id, "req_id": req_id},
    )
    assert active_count == 1
