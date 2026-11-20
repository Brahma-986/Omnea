"""TPRM Security Assessment Golden Evaluation Benchmark.

Evaluates TPRM Domain Assessment Agent and Citation Verifier across canonical
golden scenarios: Clean SOC 2, Qualified Opinion, Incomplete/Missing Domains,
Adversarial Hostile Documents, and Tampered Quote Invariants.
"""

from __future__ import annotations

import pytest

from procure_agents.approval.verifier import CitationVerifier
from procure_agents.tprm.extractor import (
    TPRM_CONTROL_DOMAINS,
    TprmDomainAssessmentAgent,
)
from procure_core.security.isolated_extractor import (
    IsolatedPdfExtractor,
)
from procure_core.security.pdf_scanner import StructuralPdfScanner
from tests.fakes.pdf_factory import (
    build_clean_soc2_pdf,
    build_hostile_pdf_javascript,
    build_qualified_soc2_pdf,
    build_raw_pdf_bytes,
)


@pytest.mark.asyncio
async def test_tprm_golden_eval_suite() -> None:
    """Run full TPRM golden evaluation benchmark and report accuracy metrics."""
    extractor = IsolatedPdfExtractor()
    agent = TprmDomainAssessmentAgent()

    print("\n" + "=" * 80)
    print("TPRM GOLDEN EVALUATION BENCHMARK")
    print("=" * 80)

    # -------------------------------------------------------------------------
    # Case 1: Clean SOC 2 Type II
    # -------------------------------------------------------------------------
    pdf_clean = build_clean_soc2_pdf("Datadog, Inc.")
    doc_clean = extractor.extract(pdf_clean)
    res_clean = await agent.assess_document(doc_clean, "SOC2_TYPE_II", "Datadog, Inc.")

    assert res_clean.auditor_opinion == "unqualified", (
        f"Expected unqualified, got {res_clean.auditor_opinion}"
    )
    assert res_clean.overall_status == "compliant", (
        f"Expected compliant, got {res_clean.overall_status}"
    )
    assert res_clean.risk_tier == "low"

    # All 6 domains must be covered
    covered_domains = {f.domain for f in res_clean.findings}
    for d in TPRM_CONTROL_DOMAINS:
        assert d in covered_domains, f"Domain {d} missing from findings"

    # Precision invariant: 100% of satisfied findings must have verbatim verifiable quotes
    for f in res_clean.findings:
        if f.status == "satisfied":
            assert f.verified is True
            page_text = doc_clean.get_page_text(f.page_number)
            assert f.quote in page_text, f"Quote for {f.domain} not found in page {f.page_number}"
            assert CitationVerifier.verify_quote_substring(f.quote, page_text) is True

    print(
        "✓ Case 1 (Clean SOC 2 Type II): 6/6 domains verified, Unqualified opinion, Zero hallucinations"
    )

    # -------------------------------------------------------------------------
    # Case 2: Qualified Opinion SOC 2
    # -------------------------------------------------------------------------
    pdf_qualified = build_qualified_soc2_pdf("Legacy Cloud Inc.")
    doc_qualified = extractor.extract(pdf_qualified)
    res_qualified = await agent.assess_document(doc_qualified, "SOC2_TYPE_II", "Legacy Cloud Inc.")

    assert res_qualified.auditor_opinion == "qualified", (
        f"Expected qualified opinion, got {res_qualified.auditor_opinion}"
    )
    assert res_qualified.overall_status == "qualified_opinion"
    assert res_qualified.risk_tier == "high"
    assert any("Auditor Opinion Qualification" in r for r in res_qualified.active_risks)

    print("✓ Case 2 (Qualified Opinion SOC 2): Detected qualification, Risk Tier elevated to HIGH")

    # -------------------------------------------------------------------------
    # Case 3: Incomplete Security Whitepaper (Missing Domains Honest Reporting)
    # -------------------------------------------------------------------------
    # Document only covers Access Control and Encryption
    gapped_pages = [
        "Independent Service Auditor's Report: unqualified opinion on infrastructure.",
        "Access Control: Enforces multi-factor authentication (MFA) and single sign-on.",
        "Encryption: Uses AES-256 for data at rest and TLS 1.3 for data in transit.",
    ]
    pdf_gapped = build_raw_pdf_bytes(gapped_pages)
    doc_gapped = extractor.extract(pdf_gapped)
    res_gapped = await agent.assess_document(doc_gapped, "SOC2_TYPE_II", "Partial SaaS")

    # Check that missing domains are honestly reported as not_found
    missing_findings = [f for f in res_gapped.findings if f.status == "not_found"]
    missing_names = {f.domain for f in missing_findings}
    assert "vulnerability_management" in missing_names
    assert "incident_response" in missing_names
    assert "data_retention_privacy" in missing_names

    # None of the missing findings can claim to have a quote or verified badge
    for mf in missing_findings:
        assert mf.verified is False
        assert mf.quote is None
        assert mf.page_number is None

    print(
        f"✓ Case 3 (Incomplete Whitepaper): Honest 'not_found' for 3 missing domains: {sorted(missing_names)}"
    )

    # -------------------------------------------------------------------------
    # Case 4: Adversarial Hostile PDF (Structural Zero-Trust Block)
    # -------------------------------------------------------------------------
    pdf_hostile = build_hostile_pdf_javascript()
    scan_hostile = StructuralPdfScanner.scan(pdf_hostile)
    assert scan_hostile.is_safe is False
    assert any("JAVASCRIPT" in t.upper() for t in scan_hostile.threats)

    print("✓ Case 4 (Hostile Obfuscated PDF): Quarantined at scanner stage before LLM ingestion")

    # -------------------------------------------------------------------------
    # Case 5: Altered Quote Precision Invariant (Zero-Tolerance Mutation Guard)
    # -------------------------------------------------------------------------
    p2_text = doc_clean.get_page_text(2)
    assert "multi-factor authentication" in p2_text

    # Candidate quote with 1-character tamper
    tampered_quote = "Access to production systems requires single-factor authentication (SFA)"
    fake_findings = {
        "auditor_opinion": "unqualified",
        "findings": [
            {
                "domain": "access_control",
                "status": "satisfied",
                "page_number": 2,
                "quote": tampered_quote,
                "rationale": "Tampered claim",
            }
        ],
    }
    res_tampered = agent._verify_and_build_result(
        fake_findings, doc_clean, "resp-tamp", "test-model"
    )
    ac_finding = next(f for f in res_tampered.findings if f.domain == "access_control")

    assert ac_finding.verified is False
    assert ac_finding.status == "not_found"
    assert ac_finding.quote is None

    print("✓ Case 5 (Tampered Quote Invariant): Dropped finding, verified=False, quote=None")

    # -------------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------------
    print("\n" + "-" * 80)
    print("GOLDEN EVAL SUMMARY:")
    print("  Total Benchmark Scenarios Evaluated: 5")
    print("  Citation Precision: 100.0% (Zero Hallucination Tolerance)")
    print("  Hostile Block Rate: 100.0% (Fail-Closed Quarantine)")
    print("  Honest Omission Rate: 100.0% (Unfound domains marked not_found)")
    print("=" * 80 + "\n")
