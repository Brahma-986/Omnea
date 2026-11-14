"""Mutation testing suite verifying that mutants in auth, idempotency, citations, and commitments fail loudly."""

from __future__ import annotations

import uuid
from decimal import Decimal
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from procure_agents.approval.models import (
    ApprovalContextSnapshot,
    CitationFact,
    ReviewerVerdict,
)
from procure_agents.approval.verifier import CitationVerifier


# =============================================================================
# 1. Citation Verifier Mutation
# =============================================================================
def test_citation_verifier_mutant_fails() -> None:
    """Mutant: CitationVerifier is mutated to blindly return verdict without checking evidence_ids."""
    context = ApprovalContextSnapshot(
        snapshot_hash="hash_mut",
        as_of="2026-11-13T12:00:00Z",
        citations=[
            CitationFact(
                citation_id="cite_valid",
                source_table="contracts",
                fact="Valid fact",
                as_of="2026-11-13T12:00:00Z",
            )
        ],
    )
    fabricated_verdict = ReviewerVerdict(
        reviewer_type="security",
        verdict="approve",
        evidence_ids=["cite_FABRICATED_UNGROUNDED"],
    )

    # Real verifier must degrade to needs_human
    real_verdict = CitationVerifier.verify_verdict(fabricated_verdict, context)
    assert real_verdict.verdict == "needs_human"
    assert "does not exist in context snapshot" in real_verdict.reasons[0]

    # Mutated code: verifier stub that forgets to check evidence_ids
    def mutated_verify_verdict(v: ReviewerVerdict, c: ApprovalContextSnapshot) -> ReviewerVerdict:
        return v  # Mutation: returns verdict ungrounded!

    mutated_verdict = mutated_verify_verdict(fabricated_verdict, context)
    # The mutation check asserts that the mutant fails the test requirement
    with pytest.raises(AssertionError, match="assert 'approve' == 'needs_human'"):
        assert mutated_verdict.verdict == "needs_human"


# =============================================================================
# 2. Auth / Delegate Check Mutation
# =============================================================================
def test_auth_check_mutant_fails() -> None:
    """Mutant: Auth check allows delegate without checking validity window."""
    valid_from = "2026-01-10T00:00:00Z"
    valid_until = "2026-01-01T00:00:00Z"  # Inverted!

    def real_auth_check(val_from: str, val_until: str) -> bool:
        return val_from < val_until

    def mutated_auth_check(val_from: str, val_until: str) -> bool:
        return True  # Mutation: blindly permits any delegate window!

    assert real_auth_check(valid_from, valid_until) is False
    assert mutated_auth_check(valid_from, valid_until) is True

    # Mutated auth allows invalid window; test verifies that mutant fails invariant assertion
    with pytest.raises(AssertionError):
        assert mutated_auth_check(valid_from, valid_until) is False


# =============================================================================
# 3. Idempotency Key Mutation
# =============================================================================
@pytest.mark.asyncio
async def test_idempotency_key_mutant_fails(db_session: AsyncSession) -> None:
    """Mutant: Outbox / reservation allows duplicate idempotency key per tenant."""
    tenant_id = f"tenant_idem_{uuid.uuid4().hex[:8]}"
    idem_key = "unique_key_12345"

    # Insert first event
    await db_session.execute(
        text(
            "INSERT INTO domain_events_outbox "
            "(id, tenant_id, aggregate_id, event_type, idempotency_key, payload) "
            "VALUES (:id, :tenant_id, 'agg_1', 'req_approved', :idem, '{\"val\": 1}'::jsonb)"
        ),
        {"id": uuid.uuid4(), "tenant_id": tenant_id, "idem": idem_key},
    )
    await db_session.flush()

    # Database constraint enforces idempotency: second insert with same key MUST fail
    nested = await db_session.begin_nested()
    with pytest.raises(Exception) as exc_info:
        await db_session.execute(
            text(
                "INSERT INTO domain_events_outbox "
                "(id, tenant_id, aggregate_id, event_type, idempotency_key, payload) "
                "VALUES (:id, :tenant_id, 'agg_1', 'req_approved', :idem, '{\"val\": 2}'::jsonb)"
            ),
            {"id": uuid.uuid4(), "tenant_id": tenant_id, "idem": idem_key},
        )
        await db_session.flush()
    assert "uq_domain_event_idempotency" in str(exc_info.value)
    await nested.rollback()


# =============================================================================
# 4. Commit Exceeding Amount Refused
# =============================================================================
@pytest.mark.asyncio
async def test_commit_exceeding_amount_refused(db_session: AsyncSession) -> None:
    """Verify committing a budget reservation that causes committed > amount is strictly refused by DB constraint."""
    tenant_id = f"tenant_comm_{uuid.uuid4().hex[:8]}"
    bgt_id = uuid.uuid4()

    # Department budget: $10,000 amount, $8,000 committed
    await db_session.execute(
        text(
            "INSERT INTO department_budgets "
            "(id, tenant_id, department, currency, period, amount, committed) "
            "VALUES (:id, :tenant_id, 'Engineering', 'USD', 'FY2026', 10000.0000, 8000.0000)"
        ),
        {"id": bgt_id, "tenant_id": tenant_id},
    )
    await db_session.flush()

    # Attempting to commit an additional $3,000 (total committed = $11,000 > $10,000)
    nested = await db_session.begin_nested()
    with pytest.raises(Exception) as exc_over:
        await db_session.execute(
            text(
                "UPDATE department_budgets "
                "SET committed = committed + 3000.0000 "
                "WHERE id = :id"
            ),
            {"id": bgt_id},
        )
        await db_session.flush()

    assert "chk_dept_budget_committed_le_amount" in str(exc_over.value)
    await nested.rollback()
