"""Unit tests for Stage 5 TPRM pipeline: scanners, isolated extraction, verified citations, and API."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app
from procure_agents.approval.models import SecurityReviewSummary
from procure_agents.approval.reviewers import SecurityReviewer
from procure_agents.tprm.extractor import (
    TprmDomainAssessmentAgent,
)
from procure_core.security.isolated_extractor import (
    IsolatedPdfExtractor,
)
from procure_core.security.malware_scanner import (
    EICAR_SIGNATURE,
    HeuristicMalwareScanner,
    QuarantineManager,
)
from procure_core.security.pdf_scanner import StructuralPdfScanner
from tests.fakes.pdf_factory import (
    build_clean_soc2_pdf,
    build_hostile_pdf_embedded_exe,
    build_hostile_pdf_javascript,
    build_hostile_pdf_launch,
    build_qualified_soc2_pdf,
)


@pytest.fixture
def auth_headers_security_reviewer() -> dict[str, str]:
    """Mock headers for security_reviewer role."""
    from procure_core.security.tokens import create_access_token

    tenant_id = "tenant_default"
    token = create_access_token(
        user_id="user-sec-001",
        tenant_id=tenant_id,
        email="sec@example.com",
        roles=["security_reviewer"],
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Tenant-ID": tenant_id,
        "X-CSRF-Token": "test-csrf-token",
    }


# =============================================================================
# 1. Structural PDF Scanner Tests
# =============================================================================


def test_structural_pdf_scanner_clean() -> None:
    """Clean SOC 2 PDF passes structural scanning without threats."""
    pdf_bytes = build_clean_soc2_pdf()
    res = StructuralPdfScanner.scan(pdf_bytes)
    assert res.is_safe is True
    assert len(res.threats) == 0
    assert len(res.detected_constructs) == 0


def test_structural_pdf_scanner_hex_escapes() -> None:
    """Hex-escaped hostile tokens (e.g. /#4a#61#76#61#53#63#72#69#70#74) are normalized and blocked."""
    pdf_bytes = build_hostile_pdf_javascript()
    res = StructuralPdfScanner.scan(pdf_bytes)
    assert res.is_safe is False
    assert any("JAVASCRIPT" in t.upper() for t in res.threats)
    assert "javascript" in res.detected_constructs or "js" in res.detected_constructs


def test_structural_pdf_scanner_launch_action() -> None:
    """Arbitrary command execution via /Launch action is detected and blocked."""
    pdf_bytes = build_hostile_pdf_launch()
    res = StructuralPdfScanner.scan(pdf_bytes)
    assert res.is_safe is False
    assert any("LAUNCH" in t.upper() for t in res.threats)
    assert "launch" in res.detected_constructs


def test_structural_pdf_scanner_embedded_payload() -> None:
    """Embedded executable files or PE headers trigger immediate block."""
    pdf_bytes = build_hostile_pdf_embedded_exe()
    res = StructuralPdfScanner.scan(pdf_bytes)
    assert res.is_safe is False
    assert any("EMBEDDED" in t.upper() or "EXECUTABLE" in t.upper() for t in res.threats)


# =============================================================================
# 2. Malware Scanner & Quarantine Manager Tests
# =============================================================================


def test_malware_scanner_eicar() -> None:
    """EICAR test signature is flagged as malware threat."""
    scanner = HeuristicMalwareScanner()
    res = scanner.scan_bytes(EICAR_SIGNATURE, "test.pdf")
    assert res.is_clean is False
    assert res.threat_name == "EICAR-Test-Signature"


def test_quarantine_manager_isolation(tmp_path: Path) -> None:
    """Quarantined payloads are stored with restricted permissions and manifest."""
    quarantine_dir = tmp_path / "quarantine"
    qm = QuarantineManager(quarantine_dir=quarantine_dir)

    payload = b"MALICIOUS_PAYLOAD_BYTES"
    entry = qm.quarantine(
        data=payload,
        original_filename="suspicious.pdf",
        reason="Trojan.HostilePDF",
        tenant_id="tenant-123",
    )

    assert entry["status"] == "quarantined"
    assert entry["reason"] == "Trojan.HostilePDF"

    # Verify physical file existence in quarantine store
    quarantined_path = Path(entry["quarantine_path"])
    assert quarantined_path.exists()
    assert quarantined_path.read_bytes() == payload


# =============================================================================
# 3. OS-Isolated Extractor Tests
# =============================================================================


def test_isolated_pdf_extractor_execution() -> None:
    """PDF extraction runs in isolated process with Windows Job Object limits."""
    pdf_bytes = build_clean_soc2_pdf("Datadog, Inc.")
    extractor = IsolatedPdfExtractor(memory_limit_mb=256, timeout_seconds=15)
    extracted = extractor.extract(pdf_bytes)

    assert extracted.total_pages == 6
    assert extracted.sandboxed is True
    assert extracted.total_chars > 0
    assert len(extracted.pages) == 6

    # Verify page text contains expected domain mentions
    p1 = extracted.get_page_text(1)
    assert "Independent Service Auditor's Report" in p1
    assert "unqualified opinion" in p1

    p2 = extracted.get_page_text(2)
    assert "Access Control" in p2
    assert "multi-factor authentication" in p2


# =============================================================================
# 4. TPRM Domain Assessment Agent & Altered-Quote Tests
# =============================================================================


@pytest.mark.asyncio
async def test_tprm_agent_clean_soc2_assessment() -> None:
    """Clean SOC 2 extraction validates quotes and reports unqualified opinion."""
    pdf_bytes = build_clean_soc2_pdf("Datadog, Inc.")
    extractor = IsolatedPdfExtractor()
    extracted = extractor.extract(pdf_bytes)

    agent = TprmDomainAssessmentAgent()
    result = await agent.assess_document(
        extracted_doc=extracted,
        doc_type="SOC2_TYPE_II",
        vendor_name="Datadog, Inc.",
    )

    assert result.auditor_opinion == "unqualified"
    assert result.overall_status == "compliant"
    assert result.risk_tier == "low"
    assert len(result.findings) == 6

    # Verify that every satisfied finding has a verbatim quote matching page text
    for finding in result.findings:
        if finding.status == "satisfied":
            assert finding.verified is True
            assert finding.page_number is not None
            assert finding.quote is not None
            page_text = extracted.get_page_text(finding.page_number)
            # Literal substring check
            assert finding.quote in page_text
            assert page_text[finding.start_offset : finding.end_offset] == finding.quote


def test_altered_quote_drops_finding() -> None:
    """Altering even a single character in a quoted finding drops verification."""
    extractor = IsolatedPdfExtractor()
    extracted = extractor.extract(build_clean_soc2_pdf("Datadog, Inc."))
    p2_text = extracted.get_page_text(2)

    # Legitimate quote present on Page 2
    legit_quote = "Access to production systems is strictly restricted to authorized personnel"
    assert legit_quote in p2_text

    # Altered quote: change 'authorized' to 'UNAUTHORIZED'
    altered_quote = "Access to production systems is strictly restricted to UNAUTHORIZED personnel"
    assert altered_quote not in p2_text

    parsed_data = {
        "auditor_opinion": "unqualified",
        "findings": [
            {
                "domain": "access_control",
                "status": "satisfied",
                "page_number": 2,
                "quote": altered_quote,
                "rationale": "MFA is enforced",
            }
        ],
    }

    agent = TprmDomainAssessmentAgent()
    result = agent._verify_and_build_result(parsed_data, extracted, "resp-123", "model-test")

    # Find the access_control finding
    ac_finding = next(f for f in result.findings if f.domain == "access_control")

    # Verification MUST fail and finding MUST drop to not_found with None quote
    assert ac_finding.verified is False
    assert ac_finding.status == "not_found"
    assert ac_finding.quote is None
    assert ac_finding.page_number is None
    assert "Quote verification failed" in ac_finding.rationale


# =============================================================================
# 5. Security Reviewer Approval Gate Invariants
# =============================================================================


def test_security_reviewer_blocks_unverified_or_quarantined() -> None:
    """SecurityReviewer fails-closed if security document is unscanned, missing quotes, or quarantined."""
    from decimal import Decimal

    from procure_agents.approval.models import (
        ApprovalContextSnapshot,
        ApprovalPolicyRules,
        CitationFact,
        ProcurementIntentSummary,
    )

    reviewer = SecurityReviewer(policy=ApprovalPolicyRules())

    intent = ProcurementIntentSummary(
        request_id="req-123",
        requester_id="user-req-1",
        department="Engineering",
        vendor_name="Test Vendor",
        estimated_spend=Decimal("45000.00"),
        annualized_spend=Decimal("45000.00"),
        currency="USD",
        justification="Cloud infrastructure",
        tags=[],
    )

    citation = CitationFact(
        citation_id="cit-1",
        source_table="vendor_security_reviews",
        fact="SOC 2 Type II report on file",
        as_of=datetime.utcnow().isoformat(),
    )

    # Case A: Quarantined document -> REJECT
    context_quarantined = ApprovalContextSnapshot(
        snapshot_hash="hash-1",
        as_of=datetime.utcnow().isoformat(),
        security_reviews=[
            SecurityReviewSummary(
                doc_type="SOC2_TYPE_II",
                status="quarantined",
                quarantined=True,
                is_scanned=True,
                is_fresh=True,
                has_verified_quotes=False,
                missing_domains=["access_control"],
            )
        ],
        citations=[citation],
    )
    verdict_a = reviewer.review(intent, context_quarantined)
    assert verdict_a.verdict == "reject"
    assert verdict_a.is_hard_reject is True
    assert "quarantined" in verdict_a.hard_reject_trigger

    # Case B: Document unscanned -> needs_human
    context_unscanned = ApprovalContextSnapshot(
        snapshot_hash="hash-2",
        as_of=datetime.utcnow().isoformat(),
        security_reviews=[
            SecurityReviewSummary(
                doc_type="SOC2_TYPE_II",
                status="pending_review",
                quarantined=False,
                is_scanned=False,
                is_fresh=True,
                has_verified_quotes=False,
                missing_domains=["encryption"],
            )
        ],
        citations=[citation],
    )
    verdict_b = reviewer.review(intent, context_unscanned)
    assert verdict_b.verdict == "needs_human"
    assert any("safety scan" in r.lower() for r in verdict_b.reasons)

    # Case C: Document missing verified quotes -> needs_human
    context_missing_quotes = ApprovalContextSnapshot(
        snapshot_hash="hash-3",
        as_of=datetime.utcnow().isoformat(),
        security_reviews=[
            SecurityReviewSummary(
                doc_type="SOC2_TYPE_II",
                status="compliant",
                quarantined=False,
                is_scanned=True,
                is_fresh=True,
                has_verified_quotes=False,
                missing_domains=["incident_response"],
            )
        ],
        citations=[citation],
    )
    verdict_c = reviewer.review(intent, context_missing_quotes)
    assert verdict_c.verdict == "needs_human"
    assert any("missing verified quotes" in r.lower() for r in verdict_c.reasons)

    # Case D: Scanned, fresh, all required quotes verified -> APPROVE
    context_clean = ApprovalContextSnapshot(
        snapshot_hash="hash-4",
        as_of=datetime.utcnow().isoformat(),
        security_reviews=[
            SecurityReviewSummary(
                doc_type="SOC2_TYPE_II",
                status="compliant",
                quarantined=False,
                is_scanned=True,
                is_fresh=True,
                audit_period_end=date.today().isoformat(),
                has_verified_quotes=True,
                missing_domains=[],
            )
        ],
        citations=[citation],
    )
    verdict_d = reviewer.review(intent, context_clean)
    assert verdict_d.verdict == "approve"


# =============================================================================
# 6. Security API Endpoints Tests
# =============================================================================


def test_api_upload_clean_soc2(auth_headers_security_reviewer: dict[str, str]) -> None:
    """POST /api/v1/security/documents/upload processes clean SOC 2 end-to-end."""
    client = TestClient(app)
    pdf_bytes = build_clean_soc2_pdf("Datadog, Inc.")

    files = {"file": ("soc2_datadog.pdf", pdf_bytes, "application/pdf")}
    data = {
        "vendor_name": "Datadog, Inc.",
        "doc_type": "SOC2_TYPE_II",
        "effective_date": date.today().isoformat(),
        "expiration_date": (date.today() + timedelta(days=365)).isoformat(),
    }

    resp = client.post(
        "/api/v1/security/documents/upload",
        files=files,
        data=data,
        headers=auth_headers_security_reviewer,
    )

    assert resp.status_code == 200, resp.text
    payload = resp.json()

    assert payload["vendor_name"] == "Datadog, Inc."
    assert payload["doc_type"] == "SOC2_TYPE_II"
    assert payload["status"] == "compliant"
    assert payload["is_quarantined"] is False
    assert payload["auditor_opinion"] == "unqualified"
    assert len(payload["pipeline_stages"]) == 6
    assert all(stage["status"] == "passed" for stage in payload["pipeline_stages"])
    assert len(payload["findings"]) == 6


def test_api_upload_hostile_pdf_quarantined(auth_headers_security_reviewer: dict[str, str]) -> None:
    """POST /api/v1/security/documents/upload blocks and quarantines hostile PDF."""
    client = TestClient(app)
    hostile_bytes = build_hostile_pdf_javascript()

    files = {"file": ("exploit.pdf", hostile_bytes, "application/pdf")}
    data = {
        "vendor_name": "Malicious Corp",
        "doc_type": "SOC2_TYPE_II",
        "effective_date": date.today().isoformat(),
        "expiration_date": (date.today() + timedelta(days=365)).isoformat(),
    }

    resp = client.post(
        "/api/v1/security/documents/upload",
        files=files,
        data=data,
        headers=auth_headers_security_reviewer,
    )

    assert resp.status_code == 200, resp.text
    payload = resp.json()

    assert payload["is_quarantined"] is True
    assert payload["status"] == "quarantined"
    assert "JAVASCRIPT" in (payload["quarantine_reason"] or "").upper()


def test_api_upload_qualified_opinion(auth_headers_security_reviewer: dict[str, str]) -> None:
    """POST /api/v1/security/documents/upload detects qualified auditor opinion."""
    client = TestClient(app)
    qualified_bytes = build_qualified_soc2_pdf("Legacy Corp")

    files = {"file": ("soc2_qualified.pdf", qualified_bytes, "application/pdf")}
    data = {
        "vendor_name": "Legacy Corp",
        "doc_type": "SOC2_TYPE_II",
        "effective_date": date.today().isoformat(),
        "expiration_date": (date.today() + timedelta(days=365)).isoformat(),
    }

    resp = client.post(
        "/api/v1/security/documents/upload",
        files=files,
        data=data,
        headers=auth_headers_security_reviewer,
    )

    assert resp.status_code == 200, resp.text
    payload = resp.json()

    assert payload["status"] == "qualified_opinion"
    assert payload["auditor_opinion"] == "qualified"
    assert payload["risk_tier"] == "high"


def test_api_documents_listing_and_supersession(
    auth_headers_security_reviewer: dict[str, str],
) -> None:
    """GET /api/v1/security/documents reflects supersession when new doc is uploaded."""
    client = TestClient(app)
    v_name = f"Superseded Vendor {uuid.uuid4().hex[:6]}"

    # Upload Doc 1
    resp1 = client.post(
        "/api/v1/security/documents/upload",
        files={"file": ("v1.pdf", build_clean_soc2_pdf(v_name), "application/pdf")},
        data={"vendor_name": v_name, "doc_type": "SOC2_TYPE_II"},
        headers=auth_headers_security_reviewer,
    )
    doc1 = resp1.json()

    # Upload Doc 2 for same vendor
    resp2 = client.post(
        "/api/v1/security/documents/upload",
        files={"file": ("v2.pdf", build_clean_soc2_pdf(v_name), "application/pdf")},
        data={"vendor_name": v_name, "doc_type": "SOC2_TYPE_II"},
        headers=auth_headers_security_reviewer,
    )
    doc2 = resp2.json()

    assert doc2["supersedes_id"] == doc1["id"]

    # Verify listing shows doc1 as superseded
    list_resp = client.get("/api/v1/security/documents", headers=auth_headers_security_reviewer)
    assert list_resp.status_code == 200
    docs = list_resp.json()

    d1 = next(d for d in docs if d["id"] == doc1["id"])
    d2 = next(d for d in docs if d["id"] == doc2["id"])

    assert d1["is_superseded"] is True
    assert d1["superseded_by_id"] == doc2["id"]
    assert d2["is_superseded"] is False
    assert d2["supersedes_id"] == doc1["id"]


def test_api_risk_register(auth_headers_security_reviewer: dict[str, str]) -> None:
    """GET /api/v1/security/risk-register returns risk tier and review status per vendor."""
    client = TestClient(app)
    resp = client.get("/api/v1/security/risk-register", headers=auth_headers_security_reviewer)
    assert resp.status_code == 200
    entries = resp.json()
    assert isinstance(entries, list)
    if entries:
        assert "vendor_id" in entries[0]
        assert "risk_tier" in entries[0]
        assert "overall_status" in entries[0]
