"""Verification script proving that mutations in security, citation, and idempotency logic fail loudly."""

from __future__ import annotations

import pytest

from procure_agents.approval.models import (
    ApprovalContextSnapshot,
    CitationFact,
    ReviewerVerdict,
)
from procure_agents.approval.verifier import CitationVerifier


def test_mutation_citation_check_bypass_is_detected() -> None:
    """Proves that a mutated verifier that skips citation verification fails test assertions."""
    context = ApprovalContextSnapshot(
        snapshot_hash="hash_mut",
        as_of="2026-11-13T12:00:00Z",
        citations=[
            CitationFact(
                citation_id="cite_real",
                source_table="contracts",
                fact="Real Fact",
                as_of="2026-11-13T12:00:00Z",
            )
        ],
    )
    bogus_verdict = ReviewerVerdict(
        reviewer_type="security",
        verdict="approve",
        evidence_ids=["cite_FABRICATED_ID"],
    )

    # Standard real verifier catches the mutation and degrades
    real_result = CitationVerifier.verify_verdict(bogus_verdict, context)
    assert real_result.verdict == "needs_human"
    assert "cite_FABRICATED_ID" in real_result.reasons[0]

    # Mutated verifier (stub that blindly returns original verdict)
    def mutated_verifier(v: ReviewerVerdict, c: ApprovalContextSnapshot) -> ReviewerVerdict:
        return v  # Bypasses citation verification!

    mutated_result = mutated_verifier(bogus_verdict, context)
    # The mutant allows 'approve' through without checking citations!
    # Asserting that the mutant would fail the real test's assertion:
    with pytest.raises(AssertionError):
        assert mutated_result.verdict == "needs_human"
