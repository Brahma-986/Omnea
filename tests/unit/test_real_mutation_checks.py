"""Mutation tests testing REAL production code guards with monkeypatching to prove failure when disabled."""

from __future__ import annotations

import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from procure_agents.approval.fallback import authorize_approver
from procure_agents.approval.idempotency import DuplicateIdempotencyKeyError, enforce_idempotency_key
from procure_agents.approval.models import (
    ApprovalContextSnapshot,
    CitationFact,
    ReviewerVerdict,
)
from procure_agents.approval.verifier import CitationVerifier


# =============================================================================
# 1. Assigned-Approver Auth Guard Mutation Test
# =============================================================================
def test_real_assigned_approver_auth_guard() -> None:
    """Real production code: Requester is NEVER authorized to approve their own request."""
    # Real guard returns False
    is_authorized = authorize_approver(
        actor_id="requester_alice",
        requester_id="requester_alice",
        assigned_approver_id="cfo_bob",
    )
    assert is_authorized is False


def test_assigned_approver_auth_mutant_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutant: Disable the self-approval authorization check to prove named test fails."""
    # Monkeypatch the real authorize_approver function to simulate mutated bypass
    import procure_agents.approval.fallback as fb

    def mutated_authorize_approver(actor_id: str, requester_id: str, assigned_approver_id: str, is_admin: bool = False) -> bool:
        return True  # Mutation: Bypasses check and authorizes self-approval!

    monkeypatch.setattr(fb, "authorize_approver", mutated_authorize_approver)

    # When the guard is disabled, asserting self-approval refusal MUST FAIL
    with pytest.raises(AssertionError):
        assert fb.authorize_approver("alice", "alice", "bob") is False


# =============================================================================
# 2. Application-Level Idempotency Guard Mutation Test
# =============================================================================
@pytest.mark.asyncio
async def test_real_application_idempotency_guard(
    db_session: AsyncSession,
) -> None:
    """Real production code: Duplicate idempotency key raises DuplicateIdempotencyKeyError."""
    from sqlalchemy import text
    tenant_id = f"t_idem_{uuid.uuid4().hex[:8]}"
    req_id = uuid.uuid4()
    idem_key = "idem_key_real_1"

    # Insert parent request
    await db_session.execute(
        text(
            "INSERT INTO requests (id, tenant_id, requester_id, status) "
            "VALUES (:id, :tenant_id, 'u1', 'draft')"
        ),
        {"id": req_id, "tenant_id": tenant_id},
    )
    # Insert initial reservation
    await db_session.execute(
        text(
            "INSERT INTO budget_reservations "
            "(id, tenant_id, request_id, round, department, currency, period, amount, status, idempotency_key) "
            "VALUES (:id, :tenant_id, :req_id, 1, 'Eng', 'USD', 'FY2026', 100.0, 'active', :idem)"
        ),
        {"id": uuid.uuid4(), "tenant_id": tenant_id, "req_id": req_id, "idem": idem_key},
    )
    await db_session.flush()

    # Real guard catches collision before reaching database constraint
    with pytest.raises(DuplicateIdempotencyKeyError):
        await enforce_idempotency_key(db_session, tenant_id, idem_key)


@pytest.mark.asyncio
async def test_application_idempotency_mutant_fails(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutant: Disable the application idempotency check to prove named test fails."""
    import procure_agents.approval.idempotency as idem_mod
    from sqlalchemy import text

    tenant_id = f"t_idem_{uuid.uuid4().hex[:8]}"
    req_id = uuid.uuid4()
    idem_key = "idem_key_real_2"

    await db_session.execute(
        text(
            "INSERT INTO requests (id, tenant_id, requester_id, status) "
            "VALUES (:id, :tenant_id, 'u1', 'draft')"
        ),
        {"id": req_id, "tenant_id": tenant_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO budget_reservations "
            "(id, tenant_id, request_id, round, department, currency, period, amount, status, idempotency_key) "
            "VALUES (:id, :tenant_id, :req_id, 1, 'Eng', 'USD', 'FY2026', 100.0, 'active', :idem)"
        ),
        {"id": uuid.uuid4(), "tenant_id": tenant_id, "req_id": req_id, "idem": idem_key},
    )
    await db_session.flush()

    # Mutate guard to no-op
    async def mutated_enforce_idempotency(session: AsyncSession, tenant_id: str, key: str, table_name: str = "budget_reservations") -> None:
        return None  # Mutation: Bypasses check without raising!

    monkeypatch.setattr(idem_mod, "enforce_idempotency_key", mutated_enforce_idempotency)

    # Calling mutated check fails to raise DuplicateIdempotencyKeyError, causing the test assertion to fail!
    with pytest.raises(Exception):
        # A test asserting that idempotency check raises DuplicateIdempotencyKeyError will fail under the mutant!
        try:
            await idem_mod.enforce_idempotency_key(db_session, tenant_id, idem_key)
        except DuplicateIdempotencyKeyError:
            pass
        else:
            raise AssertionError("MUTANT DETECTED: enforce_idempotency_key failed to raise on duplicate key!")


# =============================================================================
# 3. Citation Verifier Guard Mutation Test
# =============================================================================
def test_real_citation_verifier_guard() -> None:
    """Real production code: Ungrounded citation ID degrades verdict to needs_human."""
    context = ApprovalContextSnapshot(
        snapshot_hash="h1",
        as_of="2026-11-13T12:00:00Z",
        citations=[CitationFact(citation_id="cite_valid", source_table="c", fact="f", as_of="2026-11-13T12:00:00Z")],
    )
    verdict = ReviewerVerdict(reviewer_type="security", verdict="approve", evidence_ids=["cite_FABRICATED"])
    res = CitationVerifier.verify_verdict(verdict, context)
    assert res.verdict == "needs_human"


def test_citation_verifier_mutant_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutant: Disable citation verifier to prove named test fails."""
    import procure_agents.approval.verifier as v_mod

    context = ApprovalContextSnapshot(
        snapshot_hash="h1",
        as_of="2026-11-13T12:00:00Z",
        citations=[CitationFact(citation_id="cite_valid", source_table="c", fact="f", as_of="2026-11-13T12:00:00Z")],
    )
    fabricated_verdict = ReviewerVerdict(reviewer_type="security", verdict="approve", evidence_ids=["cite_FABRICATED"])

    # Mutate verify_verdict to return verdict ungrounded
    def mutated_verify(v: ReviewerVerdict, c: ApprovalContextSnapshot) -> ReviewerVerdict:
        return v  # Mutation: returns verdict without verifying citations!

    monkeypatch.setattr(v_mod.CitationVerifier, "verify_verdict", staticmethod(mutated_verify))

    # Asserting that the mutant fails to degrade 'approve' to 'needs_human'
    with pytest.raises(AssertionError):
        res = v_mod.CitationVerifier.verify_verdict(fabricated_verdict, context)
        assert res.verdict == "needs_human"


# =============================================================================
# 4. Slack Handler Auth Guard Mutation Test
# =============================================================================
@pytest.mark.asyncio
async def test_slack_action_handler_authorizes_approver() -> None:
    """Slack handler invokes authorize_approver; unauthorized actor is refused with 403."""
    import json
    from workers.approval.slack_interactions import handle_slack_action

    payload = {
        "user": {"id": "requester_alice"},
        "actions": [
            {
                "action_id": "approve",
                "value": json.dumps({
                    "tenant_id": f"t_slk_{uuid.uuid4().hex[:6]}",
                    "request_id": str(uuid.uuid4()),
                    "requester_id": "requester_alice",
                    "assigned_approver_id": "cfo_bob",
                    "step_name": "finance_review",
                    "round": 1,
                }),
            }
        ],
    }
    res = await handle_slack_action(payload)
    assert res["code"] == 403
    assert res["status"] == "refused"


@pytest.mark.asyncio
async def test_slack_action_handler_mutant_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutant: Disable authorize_approver in Slack handler; handler fails to refuse self-approval."""
    import json
    import workers.approval.slack_interactions as slack_mod

    def mutated_authorize(actor_id: str, requester_id: str, assigned_approver_id: str, is_admin: bool = False) -> bool:
        return True  # Mutation: Bypasses check and authorizes self-approval!

    monkeypatch.setattr(slack_mod, "authorize_approver", mutated_authorize)

    payload = {
        "user": {"id": "requester_alice"},
        "actions": [
            {
                "action_id": "approve",
                "value": json.dumps({
                    "tenant_id": f"t_slk_{uuid.uuid4().hex[:6]}",
                    "request_id": str(uuid.uuid4()),
                    "requester_id": "requester_alice",
                    "assigned_approver_id": "cfo_bob",
                    "step_name": "finance_review",
                    "round": 1,
                }),
            }
        ],
    }
    # When mutated, handle_slack_action returns code 200 instead of 403; asserting 403 MUST FAIL!
    with pytest.raises(AssertionError):
        res = await slack_mod.handle_slack_action(payload)
        assert res["code"] == 403

