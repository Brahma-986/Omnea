"""Integration tests for budget reservations: commit idempotency on replay, release triggers, and concurrent row-level locking."""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DB_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/procure_os"


# =============================================================================
# Helper Activity Functions for Reservation Engine
# =============================================================================
async def reserve_budget_under_lock(
    session: AsyncSession,
    tenant_id: str,
    request_id: uuid.UUID,
    round_num: int,
    department: str,
    currency: str,
    period: str,
    amount: Decimal,
    idempotency_key: str,
) -> tuple[bool, str]:
    """Acquires active budget reservation under row-level lock SELECT ... FOR UPDATE."""
    # 1. Row-level lock on budget record
    budget_row = (
        await session.execute(
            text(
                "SELECT id, amount, committed FROM department_budgets "
                "WHERE tenant_id = :tenant_id AND department = :dept AND currency = :curr AND period = :period "
                "FOR UPDATE"
            ),
            {"tenant_id": tenant_id, "dept": department, "curr": currency, "period": period},
        )
    ).fetchone()

    if not budget_row:
        return False, "Budget record not found"

    bgt_id, bgt_amount, bgt_committed = budget_row
    # Calculate active reservations currently holding budget
    active_reserved = await session.scalar(
        text(
            "SELECT COALESCE(SUM(amount), 0) FROM budget_reservations "
            "WHERE tenant_id = :tenant_id AND department = :dept AND currency = :curr AND period = :period "
            "AND status = 'active'"
        ),
        {"tenant_id": tenant_id, "dept": department, "curr": currency, "period": period},
    ) or Decimal("0.0")

    available_headroom = bgt_amount - bgt_committed - active_reserved
    if amount > available_headroom:
        return False, f"Insufficient headroom: requested {amount}, available {available_headroom}"

    # Insert active reservation
    res_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO budget_reservations "
            "(id, tenant_id, request_id, round, department, currency, period, amount, status, idempotency_key) "
            "VALUES (:id, :tenant_id, :req_id, :round, :dept, :curr, :period, :amount, 'active', :idem)"
        ),
        {
            "id": res_id,
            "tenant_id": tenant_id,
            "req_id": request_id,
            "round": round_num,
            "dept": department,
            "curr": currency,
            "period": period,
            "amount": amount,
            "idem": idempotency_key,
        },
    )
    return True, f"Reserved {amount}"


async def commit_budget_reservation_idempotent(
    session: AsyncSession,
    tenant_id: str,
    request_id: uuid.UUID,
    round_num: int,
) -> bool:
    """Commits active reservation under row lock. Idempotent on replay."""
    # Check current reservation status under FOR UPDATE
    res_row = (
        await session.execute(
            text(
                "SELECT id, department, currency, period, amount, status "
                "FROM budget_reservations "
                "WHERE tenant_id = :tenant_id AND request_id = :req_id AND round = :round "
                "FOR UPDATE"
            ),
            {"tenant_id": tenant_id, "req_id": request_id, "round": round_num},
        )
    ).fetchone()

    if not res_row:
        return False

    res_id, dept, curr, period, amount, status = res_row
    if status == "committed":
        # Replay detected: already committed, do NOT double-increment department_budgets
        return True

    if status != "active":
        return False

    # 1. Update reservation to committed
    await session.execute(
        text(
            "UPDATE budget_reservations "
            "SET status = 'committed', committed_at = clock_timestamp() "
            "WHERE id = :id"
        ),
        {"id": res_id},
    )

    # 2. Increment department committed spend
    await session.execute(
        text(
            "UPDATE department_budgets "
            "SET committed = committed + :amount "
            "WHERE tenant_id = :tenant_id AND department = :dept AND currency = :curr AND period = :period"
        ),
        {"amount": amount, "tenant_id": tenant_id, "dept": dept, "curr": curr, "period": period},
    )
    return True


async def release_budget_reservation(
    session: AsyncSession,
    tenant_id: str,
    request_id: uuid.UUID,
    round_num: int,
) -> bool:
    """Releases active reservation under lock (used on T3 expiry, reject, or new round)."""
    res = await session.execute(
        text(
            "UPDATE budget_reservations "
            "SET status = 'released', released_at = clock_timestamp() "
            "WHERE tenant_id = :tenant_id AND request_id = :req_id AND round = :round AND status = 'active'"
        ),
        {"tenant_id": tenant_id, "req_id": request_id, "round": round_num},
    )
    return bool(res.rowcount and res.rowcount > 0)


# =============================================================================
# Test Cases
# =============================================================================
@pytest.mark.asyncio
async def test_reservation_commit_once_on_replay(db_session: AsyncSession) -> None:
    """Verify that committing a reservation is idempotent on workflow replay."""
    tenant_id = f"t_rep_{uuid.uuid4().hex[:8]}"
    req_id = uuid.uuid4()
    dept = "Engineering"
    curr = "USD"
    period = "FY2026"

    # Setup department budget: $10,000 amount, $0 committed
    await db_session.execute(
        text(
            "INSERT INTO department_budgets (id, tenant_id, department, currency, period, amount, committed) "
            "VALUES (:id, :tenant_id, :dept, :curr, :period, 10000.0, 0.0)"
        ),
        {"id": uuid.uuid4(), "tenant_id": tenant_id, "dept": dept, "curr": curr, "period": period},
    )
    # Insert parent request
    await db_session.execute(
        text(
            "INSERT INTO requests (id, tenant_id, requester_id, status) VALUES (:id, :tenant_id, 'u1', 'draft')"
        ),
        {"id": req_id, "tenant_id": tenant_id},
    )
    # Acquire active reservation ($2,500)
    ok, _ = await reserve_budget_under_lock(
        db_session, tenant_id, req_id, 1, dept, curr, period, Decimal("2500.00"), "idem_res_rep"
    )
    assert ok is True
    await db_session.flush()

    # Commit 1
    committed_1 = await commit_budget_reservation_idempotent(db_session, tenant_id, req_id, 1)
    assert committed_1 is True
    await db_session.flush()

    # Verify committed spend is $2,500
    committed_amt_1 = await db_session.scalar(
        text(
            "SELECT committed FROM department_budgets WHERE tenant_id = :tenant_id AND department = :dept"
        ),
        {"tenant_id": tenant_id, "dept": dept},
    )
    assert committed_amt_1 == Decimal("2500.0000")

    # Commit 2 (Simulated Workflow Replay)
    committed_2 = await commit_budget_reservation_idempotent(db_session, tenant_id, req_id, 1)
    assert committed_2 is True
    await db_session.flush()

    # Invariant: committed spend MUST STILL be $2,500 (NOT $5,000!)
    committed_amt_2 = await db_session.scalar(
        text(
            "SELECT committed FROM department_budgets WHERE tenant_id = :tenant_id AND department = :dept"
        ),
        {"tenant_id": tenant_id, "dept": dept},
    )
    assert committed_amt_2 == Decimal("2500.0000")


@pytest.mark.asyncio
async def test_reservation_release_on_t3_expiry(db_session: AsyncSession) -> None:
    """Verify active reservation is released cleanly when T3 timeout fires."""
    tenant_id = f"t_t3_{uuid.uuid4().hex[:8]}"
    req_id = uuid.uuid4()
    dept = "Sales"
    curr = "USD"
    period = "FY2026"

    await db_session.execute(
        text(
            "INSERT INTO department_budgets (id, tenant_id, department, currency, period, amount, committed) "
            "VALUES (:id, :tenant_id, :dept, :curr, :period, 20000.0, 0.0)"
        ),
        {"id": uuid.uuid4(), "tenant_id": tenant_id, "dept": dept, "curr": curr, "period": period},
    )
    await db_session.execute(
        text(
            "INSERT INTO requests (id, tenant_id, requester_id, status) VALUES (:id, :tenant_id, 'u1', 'draft')"
        ),
        {"id": req_id, "tenant_id": tenant_id},
    )
    await db_session.flush()

    # Reserve $4,000
    ok, _ = await reserve_budget_under_lock(
        db_session, tenant_id, req_id, 1, dept, curr, period, Decimal("4000.00"), "idem_t3"
    )
    assert ok is True
    await db_session.flush()

    # T3 timeout fires -> release
    released = await release_budget_reservation(db_session, tenant_id, req_id, 1)
    assert released is True
    await db_session.flush()

    status = await db_session.scalar(
        text(
            "SELECT status FROM budget_reservations WHERE tenant_id = :tenant_id AND request_id = :req_id AND round = 1"
        ),
        {"tenant_id": tenant_id, "req_id": req_id},
    )
    assert status == "released"


@pytest.mark.asyncio
async def test_reservation_release_on_reject(db_session: AsyncSession) -> None:
    """Verify active reservation is released cleanly when a reviewer hard-rejects or human rejects."""
    tenant_id = f"t_rej_{uuid.uuid4().hex[:8]}"
    req_id = uuid.uuid4()
    dept = "Marketing"
    curr = "USD"
    period = "FY2026"

    await db_session.execute(
        text(
            "INSERT INTO department_budgets (id, tenant_id, department, currency, period, amount, committed) "
            "VALUES (:id, :tenant_id, :dept, :curr, :period, 15000.0, 0.0)"
        ),
        {"id": uuid.uuid4(), "tenant_id": tenant_id, "dept": dept, "curr": curr, "period": period},
    )
    await db_session.execute(
        text(
            "INSERT INTO requests (id, tenant_id, requester_id, status) VALUES (:id, :tenant_id, 'u1', 'draft')"
        ),
        {"id": req_id, "tenant_id": tenant_id},
    )
    await db_session.flush()

    # Reserve $3,000
    ok, _ = await reserve_budget_under_lock(
        db_session, tenant_id, req_id, 1, dept, curr, period, Decimal("3000.00"), "idem_rej"
    )
    assert ok is True
    await db_session.flush()

    # Rejection occurs -> release reservation
    released = await release_budget_reservation(db_session, tenant_id, req_id, 1)
    assert released is True
    await db_session.flush()

    status = await db_session.scalar(
        text(
            "SELECT status FROM budget_reservations WHERE tenant_id = :tenant_id AND request_id = :req_id AND round = 1"
        ),
        {"tenant_id": tenant_id, "req_id": req_id},
    )
    assert status == "released"


@pytest.mark.asyncio
async def test_reservation_release_on_new_round(db_session: AsyncSession) -> None:
    """Verify Round 1 reservation is released before Round 2 acquires a fresh reservation."""
    tenant_id = f"t_rnd_{uuid.uuid4().hex[:8]}"
    req_id = uuid.uuid4()
    dept = "Operations"
    curr = "USD"
    period = "FY2026"

    await db_session.execute(
        text(
            "INSERT INTO department_budgets (id, tenant_id, department, currency, period, amount, committed) "
            "VALUES (:id, :tenant_id, :dept, :curr, :period, 10000.0, 0.0)"
        ),
        {"id": uuid.uuid4(), "tenant_id": tenant_id, "dept": dept, "curr": curr, "period": period},
    )
    await db_session.execute(
        text(
            "INSERT INTO requests (id, tenant_id, requester_id, status) VALUES (:id, :tenant_id, 'u1', 'draft')"
        ),
        {"id": req_id, "tenant_id": tenant_id},
    )
    await db_session.flush()

    # Round 1 reservation ($5,000)
    await reserve_budget_under_lock(
        db_session, tenant_id, req_id, 1, dept, curr, period, Decimal("5000.00"), "idem_rnd_r1"
    )
    await db_session.flush()

    # Transition to new round: Round 1 reservation is released
    released = await release_budget_reservation(db_session, tenant_id, req_id, 1)
    assert released is True
    await db_session.flush()

    # Round 2 reservation acquired ($5,000)
    ok_r2, _ = await reserve_budget_under_lock(
        db_session, tenant_id, req_id, 2, dept, curr, period, Decimal("5000.00"), "idem_rnd_r2"
    )
    assert ok_r2 is True
    await db_session.flush()

    status_r1 = await db_session.scalar(
        text(
            "SELECT status FROM budget_reservations WHERE tenant_id = :tenant_id AND request_id = :req_id AND round = 1"
        ),
        {"tenant_id": tenant_id, "req_id": req_id},
    )
    status_r2 = await db_session.scalar(
        text(
            "SELECT status FROM budget_reservations WHERE tenant_id = :tenant_id AND request_id = :req_id AND round = 2"
        ),
        {"tenant_id": tenant_id, "req_id": req_id},
    )
    assert status_r1 == "released"
    assert status_r2 == "active"


@pytest.mark.asyncio
async def test_two_concurrent_requests_nearly_exhausted_budget() -> None:
    """Verify row-level locking handles two concurrent requests on nearly exhausted budget.
    Headroom is $5,000. Req A requests $4,000. Req B requests $3,000.
    Tests both orderings (A then B, and B then A), as well as concurrent interleaving.
    """
    engine = create_async_engine(DB_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # 1. Test Order 1: A commits first, B must be rejected due to insufficient headroom
    tenant_1 = f"t_ord1_{uuid.uuid4().hex[:8]}"
    dept = "Product"
    curr = "USD"
    period = "FY2026"
    req_a1 = uuid.uuid4()
    req_b1 = uuid.uuid4()

    async with session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO department_budgets (id, tenant_id, department, currency, period, amount, committed) "
                "VALUES (:id, :tenant_id, :dept, :curr, :period, 10000.0, 5000.0)"
            ),
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant_1,
                "dept": dept,
                "curr": curr,
                "period": period,
            },
        )
        await s.execute(
            text(
                "INSERT INTO requests (id, tenant_id, requester_id, status) VALUES (:id, :t, 'u1', 'draft')"
            ),
            {"id": req_a1, "t": tenant_1},
        )
        await s.execute(
            text(
                "INSERT INTO requests (id, tenant_id, requester_id, status) VALUES (:id, :t, 'u2', 'draft')"
            ),
            {"id": req_b1, "t": tenant_1},
        )
        await s.commit()

    async with session_factory() as s_a:
        async with s_a.begin():
            ok_a, _ = await reserve_budget_under_lock(
                s_a, tenant_1, req_a1, 1, dept, curr, period, Decimal("4000.00"), "idem_ord1_a"
            )
            assert ok_a is True

    async with session_factory() as s_b:
        async with s_b.begin():
            ok_b, reason_b = await reserve_budget_under_lock(
                s_b, tenant_1, req_b1, 1, dept, curr, period, Decimal("3000.00"), "idem_ord1_b"
            )
            # 5000 - 4000 = 1000 headroom < 3000 requested -> REJECTED
            assert ok_b is False
            assert "Insufficient headroom" in reason_b

    # 2. Test Order 2: B commits first, A must be rejected due to insufficient headroom
    tenant_2 = f"t_ord2_{uuid.uuid4().hex[:8]}"
    req_a2 = uuid.uuid4()
    req_b2 = uuid.uuid4()

    async with session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO department_budgets (id, tenant_id, department, currency, period, amount, committed) "
                "VALUES (:id, :tenant_id, :dept, :curr, :period, 10000.0, 5000.0)"
            ),
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant_2,
                "dept": dept,
                "curr": curr,
                "period": period,
            },
        )
        await s.execute(
            text(
                "INSERT INTO requests (id, tenant_id, requester_id, status) VALUES (:id, :t, 'u1', 'draft')"
            ),
            {"id": req_a2, "t": tenant_2},
        )
        await s.execute(
            text(
                "INSERT INTO requests (id, tenant_id, requester_id, status) VALUES (:id, :t, 'u2', 'draft')"
            ),
            {"id": req_b2, "t": tenant_2},
        )
        await s.commit()

    async with session_factory() as s_b:
        async with s_b.begin():
            ok_b2, _ = await reserve_budget_under_lock(
                s_b, tenant_2, req_b2, 1, dept, curr, period, Decimal("3000.00"), "idem_ord2_b"
            )
            assert ok_b2 is True

    async with session_factory() as s_a:
        async with s_a.begin():
            ok_a2, reason_a2 = await reserve_budget_under_lock(
                s_a, tenant_2, req_a2, 1, dept, curr, period, Decimal("4000.00"), "idem_ord2_a"
            )
            # 5000 - 3000 = 2000 headroom < 4000 requested -> REJECTED
            assert ok_a2 is False
            assert "Insufficient headroom" in reason_a2

    # 3. Concurrent Interleaving (asyncio.gather across two parallel sessions)
    tenant_conc = f"t_conc_{uuid.uuid4().hex[:8]}"
    req_ac = uuid.uuid4()
    req_bc = uuid.uuid4()

    async with session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO department_budgets (id, tenant_id, department, currency, period, amount, committed) "
                "VALUES (:id, :tenant_id, :dept, :curr, :period, 10000.0, 5000.0)"
            ),
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant_conc,
                "dept": dept,
                "curr": curr,
                "period": period,
            },
        )
        await s.execute(
            text(
                "INSERT INTO requests (id, tenant_id, requester_id, status) VALUES (:id, :t, 'u1', 'draft')"
            ),
            {"id": req_ac, "t": tenant_conc},
        )
        await s.execute(
            text(
                "INSERT INTO requests (id, tenant_id, requester_id, status) VALUES (:id, :t, 'u2', 'draft')"
            ),
            {"id": req_bc, "t": tenant_conc},
        )
        await s.commit()

    async def attempt_reservation(req_id: uuid.UUID, amount: Decimal, idem: str) -> bool:
        async with session_factory() as sess:
            async with sess.begin():
                success, _ = await reserve_budget_under_lock(
                    sess, tenant_conc, req_id, 1, dept, curr, period, amount, idem
                )
                return success

    results = await asyncio.gather(
        attempt_reservation(req_ac, Decimal("4000.00"), "idem_conc_A"),
        attempt_reservation(req_bc, Decimal("3000.00"), "idem_conc_B"),
    )
    assert sorted(results) == [False, True]

    await engine.dispose()
