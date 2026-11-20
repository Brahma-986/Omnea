"""TPRM Domain Assessment Agent extracting grounded control findings with verbatim quote verification."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from procure_agents.approval.verifier import CitationVerifier
from procure_agents.llm.client import ResilientLLMClient
from procure_core.security.isolated_extractor import ExtractedDocument

logger = logging.getLogger("procure_agents.tprm.extractor")

TPRM_CONTROL_DOMAINS = [
    "access_control",
    "encryption",
    "vulnerability_management",
    "incident_response",
    "data_retention_privacy",
    "auditor_opinion",
]


@dataclass
class TprmFinding:
    """Individual control domain assessment finding."""

    domain: str
    status: str  # satisfied, qualified, remediation_required, not_found
    page_number: int | None
    quote: str | None
    start_offset: int | None
    end_offset: int | None
    verified: bool
    rationale: str
    risk_level: str = "low"  # low, medium, high


@dataclass
class TprmAssessmentResult:
    """Overall assessment output from TPRM extraction pipeline."""

    findings: list[TprmFinding] = field(default_factory=list)
    auditor_opinion: str = "unqualified"
    overall_status: str = (
        "compliant"  # compliant, qualified_opinion, remediation_required, quarantined
    )
    risk_tier: str = "low"  # low, medium, high, critical
    active_risks: list[str] = field(default_factory=list)
    verified_quote_count: int = 0
    missing_domains: list[str] = field(default_factory=list)
    provider_response_id: str | None = None
    model_name: str | None = None


class TprmDomainAssessmentAgent:
    """Extracts and verifies third-party risk management findings from security documents."""

    SYSTEM_PROMPT = """You are Omnea's Senior TPRM (Third-Party Risk Management) Security Reviewer.
Your role is to assess vendor security reports (SOC 2, ISO 27001, DPA, Pen Tests) across 6 control domains:
1. access_control: MFA enforcement, Single Sign-On (SSO), RBAC.
2. encryption: In-transit (TLS 1.2+) and at-rest (AES-256) encryption.
3. vulnerability_management: Periodic penetration testing, vulnerability scanning.
4. incident_response: 24/7 CSIRT, defined breach notification window to customers.
5. data_retention_privacy: Customer data deletion, retention SLAs, data segregation.
6. auditor_opinion: Independent auditor's report opinion. Must identify whether the opinion is 'unqualified' (clean), 'qualified' (scope exception or control failure), 'adverse', or 'disclaimer'.

CRITICAL INVARIANTS:
1. Grounded Citations Only: Every finding MUST cite an EXACT verbatim quote from the text of the specified page.
2. Honest Absence: If a control domain is NOT covered or missing in the document, you MUST set:
   status: "not_found"
   page_number: null
   quote: null
   Do NOT invent, fake, or extrapolate missing controls!
3. Qualified Opinion: If the report contains modified opinions, scope exceptions, or auditor qualifications, set:
   domain: "auditor_opinion"
   status: "qualified"
   and cite the auditor's qualification statement.

Output valid JSON ONLY with this schema:
{
  "auditor_opinion": "unqualified" | "qualified" | "adverse" | "disclaimer",
  "findings": [
    {
      "domain": "access_control" | "encryption" | "vulnerability_management" | "incident_response" | "data_retention_privacy" | "auditor_opinion",
      "status": "satisfied" | "qualified" | "remediation_required" | "not_found",
      "page_number": <int or null>,
      "quote": "<exact verbatim quote or null>",
      "rationale": "<concise explanation>"
    }
  ]
}
"""

    def __init__(self, llm_client: ResilientLLMClient | None = None) -> None:
        self.llm_client = llm_client or ResilientLLMClient()

    async def assess_document(
        self,
        extracted_doc: ExtractedDocument,
        doc_type: str = "SOC2_TYPE_II",
        vendor_name: str = "Vendor",
        force_llm: bool = False,
    ) -> TprmAssessmentResult:
        """Run LLM extraction and deterministic quote verification over extracted document pages."""
        # Prepare page-partitioned text prompt
        doc_text_parts = []
        for p in extracted_doc.pages:
            doc_text_parts.append(f"--- PAGE {p.page_number} ---\n{p.text}")
        combined_doc_text = "\n\n".join(doc_text_parts)

        # Truncate if document exceeds safe prompt window (keep first ~30k chars)
        if len(combined_doc_text) > 35000:
            combined_doc_text = (
                combined_doc_text[:35000] + "\n\n[... Document truncated for LLM context ...]"
            )

        user_prompt = f"""Assess the following {doc_type} report for vendor '{vendor_name}'.
Extract control findings for each of the 6 required domains with verbatim quotes and page numbers.

DOCUMENT TEXT:
{combined_doc_text}
"""
        provider_resp_id = None
        model_used = None
        raw_parsed: dict[str, Any] = {}

        use_heuristic = (
            not getattr(self.llm_client.settings, "LLM_API_KEY", None)
            or os.environ.get("APP_ENV") == "test"
            or getattr(self.llm_client.settings, "APP_ENV", "") == "test"
        ) and not force_llm

        if use_heuristic:
            logger.info(
                "Using grounded heuristic extraction (test environment or unset LLM_API_KEY)"
            )
            raw_parsed = self._heuristic_fallback_assessment(extracted_doc, doc_type)
            provider_resp_id = "grounded-heuristic-resp"
            model_used = "heuristic-extractor"
            return self._verify_and_build_result(
                raw_parsed, extracted_doc, provider_resp_id, model_used
            )

        try:
            resp = await self.llm_client.generate(
                prompt=user_prompt,
                system_prompt=self.SYSTEM_PROMPT,
                temperature=0.0,
                max_tokens=2048,
            )
            provider_resp_id = (
                getattr(resp, "response_id", None) or f"gemini_{int(resp.latency_ms)}ms"
            )
            model_used = getattr(resp, "model", "gemini/gemini-3.1-flash-lite")

            # Extract JSON from response text
            text_content = resp.content.strip()
            if "```json" in text_content:
                text_content = text_content.split("```json")[1].split("```")[0].strip()
            elif "```" in text_content:
                text_content = text_content.split("```")[1].split("```")[0].strip()

            raw_parsed = json.loads(text_content)
        except Exception as exc:
            logger.warning("LLM assessment call failed (%s); performing heuristic extraction", exc)
            raw_parsed = self._heuristic_fallback_assessment(extracted_doc, doc_type)

        return self._verify_and_build_result(
            raw_parsed, extracted_doc, provider_resp_id, model_used
        )

    def _verify_and_build_result(
        self,
        parsed_data: dict[str, Any],
        doc: ExtractedDocument,
        response_id: str | None,
        model_name: str | None,
    ) -> TprmAssessmentResult:
        """Deterministically verify each extracted quote against literal document page text."""
        auditor_opinion = parsed_data.get("auditor_opinion", "unqualified").lower()
        findings_in = parsed_data.get("findings", [])

        verified_findings: list[TprmFinding] = []
        covered_domains = set()
        active_risks: list[str] = []
        verified_count = 0

        # Build page map for fast substring searching
        page_map = {p.page_number: p.text for p in doc.pages}

        for item in findings_in:
            domain = item.get("domain", "")
            if domain not in TPRM_CONTROL_DOMAINS:
                continue

            status = item.get("status", "not_found")
            page_num = item.get("page_number")
            raw_quote = item.get("quote")
            rationale = item.get("rationale", "")

            # If not found or empty quote, honestly record as not found
            if status == "not_found" or not raw_quote or page_num is None:
                verified_findings.append(
                    TprmFinding(
                        domain=domain,
                        status="not_found",
                        page_number=None,
                        quote=None,
                        start_offset=None,
                        end_offset=None,
                        verified=False,
                        rationale=rationale
                        or f"Control domain '{domain}' not addressed in document.",
                        risk_level="medium" if domain != "auditor_opinion" else "low",
                    )
                )
                covered_domains.add(domain)
                active_risks.append(f"Missing control domain: {domain.replace('_', ' ').title()}")
                continue

            page_text = page_map.get(page_num, "")

            # Exact Literal Substring Verification (TAMPER / ALTERED QUOTE GUARD)
            is_verified = False
            start_off = None
            end_off = None

            if page_text and CitationVerifier.verify_quote_substring(raw_quote, page_text):
                is_verified = True
                start_off = page_text.find(raw_quote)
                end_off = start_off + len(raw_quote)
                verified_count += 1
            else:
                # Quote was hallucinated, altered, or placed on wrong page
                logger.warning(
                    "Quote verification failed for domain '%s' on page %s: quote not found verbatim in page text.",
                    domain,
                    page_num,
                )
                # Tampered or altered quote drops verification and finding status degrades to unverified / not_found
                status = "not_found"
                rationale = (
                    f"Quote verification failed: verbatim text not found on page {page_num}."
                )

            risk_level = "low"
            if status in ("qualified", "remediation_required"):
                risk_level = "high"
                active_risks.append(
                    f"{domain.replace('_', ' ').title()}: {status.replace('_', ' ').title()}"
                )
            elif status == "not_found":
                risk_level = "medium"

            verified_findings.append(
                TprmFinding(
                    domain=domain,
                    status=status,
                    page_number=page_num if is_verified else None,
                    quote=raw_quote if is_verified else None,
                    start_offset=start_off,
                    end_offset=end_off,
                    verified=is_verified,
                    rationale=rationale,
                    risk_level=risk_level,
                )
            )
            covered_domains.add(domain)

        # Check for any missing canonical domains and add them honestly as not_found
        for domain in TPRM_CONTROL_DOMAINS:
            if domain not in covered_domains:
                verified_findings.append(
                    TprmFinding(
                        domain=domain,
                        status="not_found",
                        page_number=None,
                        quote=None,
                        start_offset=None,
                        end_offset=None,
                        verified=False,
                        rationale=f"Domain '{domain}' was not detected in the provided documentation.",
                        risk_level="medium",
                    )
                )
                active_risks.append(f"Missing control domain: {domain.replace('_', ' ').title()}")

        # Determine overall status and risk tier
        if auditor_opinion in ("qualified", "adverse", "disclaimer"):
            overall_status = "qualified_opinion"
            risk_tier = "high"
            active_risks.append(f"Auditor Opinion Qualification: {auditor_opinion.upper()}")
        elif any(f.status == "remediation_required" for f in verified_findings):
            overall_status = "remediation_required"
            risk_tier = "high"
        elif any(f.status == "not_found" for f in verified_findings):
            overall_status = "compliant"
            risk_tier = "medium" if len(active_risks) <= 2 else "high"
        else:
            overall_status = "compliant"
            risk_tier = "low"

        missing_list = [f.domain for f in verified_findings if f.status == "not_found"]

        return TprmAssessmentResult(
            findings=verified_findings,
            auditor_opinion=auditor_opinion,
            overall_status=overall_status,
            risk_tier=risk_tier,
            active_risks=active_risks,
            verified_quote_count=verified_count,
            missing_domains=missing_list,
            provider_response_id=response_id,
            model_name=model_name,
        )

    def _heuristic_fallback_assessment(
        self,
        doc: ExtractedDocument,
        doc_type: str,
    ) -> dict[str, Any]:
        """Deterministic heuristic fallback when external LLM is offline or in dry runs."""
        _ = doc_type
        full_text = doc.full_text.lower()
        findings = []

        # Heuristic auditor opinion: distinguish "qualified opinion" from "unqualified opinion"
        opinion = "unqualified"
        is_explicit_unqualified = "unqualified opinion" in full_text
        has_basis_for_qualified = "basis for qualified opinion" in full_text
        has_qualified_phrase = bool(re.search(r"(?<!un)qualified\s+opinion", full_text))

        if (
            has_basis_for_qualified
            or (has_qualified_phrase and not is_explicit_unqualified)
            or "except for" in full_text
            or "scope limitation" in full_text
        ):
            opinion = "qualified"

        # Search pages for keywords
        domain_keywords = {
            "access_control": ["mfa", "multi-factor", "role-based access", "single sign-on", "sso"],
            "encryption": ["tls 1.2", "tls 1.3", "aes-256", "encryption at rest", "in-transit"],
            "vulnerability_management": [
                "penetration test",
                "pen test",
                "vulnerability scan",
                "cve",
            ],
            "incident_response": [
                "incident response",
                "csirt",
                "breach notification",
                "hours of notice",
            ],
            "data_retention_privacy": [
                "data deletion",
                "retention period",
                "gdpr",
                "ccpa",
                "segregation",
            ],
        }

        for domain, kws in domain_keywords.items():
            matched = False
            for page in doc.pages:
                p_text = page.text
                p_lower = p_text.lower()
                for kw in kws:
                    if kw in p_lower:
                        # Find a sentence containing the keyword
                        sentences = re.split(r"(?<=[.!?])\s+", p_text)
                        for s in sentences:
                            if kw in s.lower() and len(s) > 20:
                                findings.append(
                                    {
                                        "domain": domain,
                                        "status": "satisfied",
                                        "page_number": page.page_number,
                                        "quote": s.strip(),
                                        "rationale": f"Verified implementation of {kw} on page {page.page_number}.",
                                    }
                                )
                                matched = True
                                break
                    if matched:
                        break
                if matched:
                    break
            if not matched:
                findings.append(
                    {
                        "domain": domain,
                        "status": "not_found",
                        "page_number": None,
                        "quote": None,
                        "rationale": f"No control evidence identified for {domain}.",
                    }
                )

        # Auditor opinion finding
        opinion_quote = None
        opinion_page = None
        for page in doc.pages:
            p_text = page.text
            if "opinion" in p_text.lower():
                sentences = re.split(r"(?<=[.!?])\s+", p_text)
                for s in sentences:
                    if "opinion" in s.lower() and len(s) > 20:
                        opinion_quote = s.strip()
                        opinion_page = page.page_number
                        break
            if opinion_quote:
                break

        findings.append(
            {
                "domain": "auditor_opinion",
                "status": "qualified" if opinion == "qualified" else "satisfied",
                "page_number": opinion_page,
                "quote": opinion_quote,
                "rationale": f"Auditor issued {opinion} opinion.",
            }
        )

        return {"auditor_opinion": opinion, "findings": findings}
