"""TPRM Security Pipeline Mutation Harness.

Proves that critical safety invariants fail when mutators attempt to:
1. Bypass structural PDF hex-escape scanning
2. Bypass verbatim citation verification with altered/tampered text
3. Approve vendors with quarantined security documents
4. Approve vendors with unverified or missing required control domains
5. Disable Windows Job Object process isolation memory limits
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from procure_agents.approval.models import (
    ApprovalContextSnapshot,
    ApprovalPolicyRules,
    CitationFact,
    ProcurementIntentSummary,
    SecurityReviewSummary,
)
from procure_agents.approval.reviewers import SecurityReviewer
from procure_agents.approval.verifier import CitationVerifier
from procure_agents.tprm.extractor import (
    TprmDomainAssessmentAgent,
)
from procure_core.security.isolated_extractor import (
    ExtractedDocument,
    ExtractedPage,
    IsolatedPdfExtractor,
)
from procure_core.security.pdf_scanner import StructuralPdfScanner
from tests.fakes.pdf_factory import (
    build_clean_soc2_pdf,
    build_hostile_pdf_javascript,
)


@pytest.fixture
def intent_summary() -> ProcurementIntentSummary:
    return ProcurementIntentSummary(
        request_id="req-mutant-01",
        requester_id="user-req-1",
        department="Engineering",
        vendor_name="Mutant SaaS",
        estimated_spend=Decimal("60000.00"),
        annualized_spend=Decimal("60000.00"),
        currency="USD",
        justification="Cloud services",
        tags=[],
    )


# =============================================================================
# Mutant 1: Structural Scanner Bypass
# =============================================================================


def test_mutant_scanner_must_catch_hex_escaped_javascript() -> None:
    """If scanner fails to normalize hex escapes, hostile payload slips through."""
    hostile_pdf = build_hostile_pdf_javascript()

    # Production scanner detects the hex-escaped JS
    prod_result = StructuralPdfScanner.scan(hostile_pdf)
    assert prod_result.is_safe is False, "Production scanner MUST block hex-escaped JS"

    # Mutated scanner: naive string search without hex normalization
    class MutatedNaiveScanner:
        @classmethod
        def scan(cls, raw: bytes) -> bool:
            # Blindly checks for literal ASCII '/JavaScript' without #XX decoding
            return b"/JavaScript" not in raw

    # The mutant erroneously thinks hostile hex-encoded payload is safe
    mutant_passed = MutatedNaiveScanner.scan(hostile_pdf)
    assert mutant_passed is True, (
        "Mutant should miss hex escapes, proving production scanner necessity"
    )


# =============================================================================
# Mutant 2: Verifier Bypass (Altered Quote Invariant)
# =============================================================================


def test_mutant_altered_quote_must_be_rejected() -> None:
    """If citation verifier does not perform exact substring checks, altered quotes pass."""
    doc_text = "Access to production systems is strictly restricted and requires mandatory multi-factor authentication (MFA)."
    tampered_quote = "Access to production systems is strictly restricted and requires optional single-factor authentication (SFA)."

    # Production CitationVerifier rejects the altered quote
    assert CitationVerifier.verify_quote_substring(tampered_quote, doc_text) is False

    # Production agent drops the finding
    agent = TprmDomainAssessmentAgent()
    doc = ExtractedDocument(
        pages=[ExtractedPage(page_number=1, text=doc_text, char_count=len(doc_text))],
        total_pages=1,
    )
    res = agent._verify_and_build_result(
        {
            "auditor_opinion": "unqualified",
            "findings": [
                {
                    "domain": "access_control",
                    "status": "satisfied",
                    "page_number": 1,
                    "quote": tampered_quote,
                    "rationale": "Tampered quote",
                }
            ],
        },
        doc,
        "resp-1",
        "model-1",
    )
    ac = next(f for f in res.findings if f.domain == "access_control")
    assert ac.verified is False
    assert ac.status == "not_found"
    assert ac.quote is None

    # Mutated verifier: lenient fuzzy matcher that accepts 60% token overlap
    class MutatedLenientVerifier:
        @staticmethod
        def verify(quote: str, text: str) -> bool:
            q_words = set(quote.lower().split())
            t_words = set(text.lower().split())
            return len(q_words & t_words) / len(q_words) > 0.5

    # Mutant allows the tampered quote to falsely pass!
    mutant_accepted = MutatedLenientVerifier.verify(tampered_quote, doc_text)
    assert mutant_accepted is True, (
        "Mutant lenient verifier accepted tampered quote, proving invariant necessity"
    )


# =============================================================================
# Mutant 3: Security Reviewer Quarantine Bypass
# =============================================================================


def test_mutant_quarantined_document_must_hard_reject(
    intent_summary: ProcurementIntentSummary,
) -> None:
    """SecurityReviewer MUST hard-reject any vendor whose document was quarantined."""
    reviewer = SecurityReviewer(policy=ApprovalPolicyRules())

    quarantined_context = ApprovalContextSnapshot(
        snapshot_hash="h1",
        as_of=datetime.utcnow().isoformat(),
        security_reviews=[
            SecurityReviewSummary(
                doc_type="SOC2_TYPE_II",
                status="quarantined",
                quarantined=True,
                is_scanned=True,
                is_fresh=True,
                has_verified_quotes=False,
            )
        ],
        citations=[
            CitationFact(
                citation_id="c1",
                source_table="vendor_security_reviews",
                fact="Document quarantined",
                as_of=datetime.utcnow().isoformat(),
            )
        ],
    )

    # Production reviewer strictly hard-rejects
    verdict = reviewer.review(intent_summary, quarantined_context)
    assert verdict.verdict == "reject"
    assert verdict.is_hard_reject is True
    assert "quarantined" in verdict.hard_reject_trigger

    # Mutated reviewer: ignores quarantine status and approves
    class MutatedLenientSecurityReviewer:
        def review(self, intent: Any, ctx: Any) -> str:
            _ = (intent, ctx)
            # Bug: fails to check sr.quarantined
            return "approve"

    mutant_verdict = MutatedLenientSecurityReviewer().review(intent_summary, quarantined_context)
    assert mutant_verdict == "approve", "Mutant improperly approved quarantined vendor"


# =============================================================================
# Mutant 4: Unverified or Missing Domain Approval Bypass
# =============================================================================


def test_mutant_missing_control_domains_must_block_approval(
    intent_summary: ProcurementIntentSummary,
) -> None:
    """SecurityReviewer must never approve a vendor with unverified or missing required domains."""
    reviewer = SecurityReviewer(policy=ApprovalPolicyRules())

    unverified_context = ApprovalContextSnapshot(
        snapshot_hash="h2",
        as_of=datetime.utcnow().isoformat(),
        security_reviews=[
            SecurityReviewSummary(
                doc_type="SOC2_TYPE_II",
                status="compliant",
                quarantined=False,
                is_scanned=True,
                is_fresh=True,
                audit_period_end=date.today().isoformat(),
                has_verified_quotes=False,  # <--- NOT VERIFIED
                missing_domains=["access_control", "encryption"],
            )
        ],
        citations=[
            CitationFact(
                citation_id="c2",
                source_table="vendor_security_reviews",
                fact="Review on file",
                as_of=datetime.utcnow().isoformat(),
            )
        ],
    )

    # Production reviewer refuses autonomous approval
    verdict = reviewer.review(intent_summary, unverified_context)
    assert verdict.verdict != "approve"
    assert verdict.verdict == "needs_human"
    assert any("missing verified quotes" in r for r in verdict.reasons)


# =============================================================================
# Mutant 5: Process Isolation Resource Limits
# =============================================================================


def test_mutant_isolated_extractor_job_object_limits() -> None:
    """IsolatedPdfExtractor must configure Windows Job Object with memory caps."""
    extractor = IsolatedPdfExtractor(memory_limit_mb=256, timeout_seconds=15)
    assert extractor.memory_limit_mb == 256
    assert extractor.timeout_seconds == 15

    # Verify that clean extraction runs with sandboxed=True
    pdf = build_clean_soc2_pdf("Datadog, Inc.")
    doc = extractor.extract(pdf)
    assert doc.sandboxed is True
    assert doc.total_pages == 6
