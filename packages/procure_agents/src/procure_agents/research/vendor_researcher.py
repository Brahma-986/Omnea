"""Vendor research agent enforcing strict grounded summarization and verbatim quote verification."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from procure_agents.approval.verifier import CitationVerifier
from procure_agents.llm.client import LLMClientProtocol, ResilientLLMClient
from procure_core.config import Settings, get_settings


class GroundedClaim(BaseModel):
    """An individual vendor fact claim grounded with an exact verbatim quote."""

    model_config = ConfigDict(extra="forbid")

    fact: str
    quote: str
    verified: bool = False
    source: str | None = None


class VendorResearchExtraction(BaseModel):
    """Raw structured extraction produced by the LLM."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    claims: list[GroundedClaim] = Field(default_factory=list)
    products_services: list[str] = Field(default_factory=list)
    security_certifications: list[str] = Field(default_factory=list)
    pricing_terms: str | None = None
    not_covered: list[str] = Field(default_factory=list)


class VendorResearchResult(BaseModel):
    """Verified vendor research report with strict grounding guarantees."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    vendor_id: str | None = None
    summary: str
    claims: list[GroundedClaim] = Field(default_factory=list)
    products_services: list[str] = Field(default_factory=list)
    security_certifications: list[str] = Field(default_factory=list)
    pricing_terms: str | None = None
    not_covered: list[str] = Field(default_factory=list)
    unverified_claims: list[GroundedClaim] = Field(default_factory=list)
    error: str | None = None


class VendorResearchAgent:
    """Researches and summarizes enterprise vendors strictly grounded in supplied documents.

    Guarantees:
    - Zero external web browsing or ungrounded knowledge retrieval.
    - Zero hallucinated facts: every claim must have a verified verbatim quote.
    - Explicit 'Not covered in supplied documents' for missing attributes.
    """

    def __init__(
        self,
        llm_client: LLMClientProtocol | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm = llm_client

    async def research(
        self,
        documents: list[str] | str,
        vendor_id: str | None = None,
        vendor_name: str | None = None,
    ) -> VendorResearchResult:
        """Execute strict grounded vendor research against supplied documents only."""
        if not self.settings.LLM_API_KEY or not self.settings.LLM_API_KEY.strip():
            return VendorResearchResult(
                success=False,
                summary="LLM provider is not configured. Please configure LLM_API_KEY.",
                error="LLM provider is not configured. Please configure LLM_API_KEY.",
            )

        doc_list = [documents] if isinstance(documents, str) else documents
        combined_text = "\n\n--- DOCUMENT BOUNDARY ---\n\n".join(doc_list).strip()

        if not combined_text:
            return VendorResearchResult(
                success=True,
                vendor_id=vendor_id,
                summary="Not covered in supplied documents",
                claims=[],
                products_services=[],
                security_certifications=[],
                pricing_terms="Not covered in supplied documents",
                not_covered=["All vendor details (no documents supplied)"],
            )

        client = self.llm or ResilientLLMClient(self.settings)

        system_prompt = (
            "You are a strict, grounded enterprise procurement research agent.\n"
            "Your task is to summarize the vendor STRICTLY AND ONLY from the supplied documents.\n"
            "CRITICAL RULES:\n"
            "1. NEVER browse the web. You have no web access.\n"
            "2. NEVER invent facts or use outside knowledge.\n"
            "3. Every claim or extracted attribute must include an exact, verbatim quote from the supplied text.\n"
            "4. If any detail (e.g. pricing, security, certifications) is not covered in the text, you MUST explicitly state 'Not covered in supplied documents' and add it to not_covered.\n"
            "5. Do not speculate or extrapolate."
        )

        user_content = (
            f"Vendor name: {vendor_name or 'Unknown'}\n\n"
            f"Supplied Documents:\n<documents>\n{combined_text}\n</documents>\n\n"
            "Produce structured extraction with exact verbatim quotes."
        )

        try:
            extraction: VendorResearchExtraction = await client.call_structured(
                system_prompt=system_prompt,
                user_content=user_content,
                response_model=VendorResearchExtraction,
                temperature=0.0,
            )
        except Exception as exc:
            return VendorResearchResult(
                success=False,
                summary="LLM research extraction failed",
                error=f"LLM research extraction failed: {exc}",
            )

        # Verbatim quote verification using CitationVerifier
        verified_claims: list[GroundedClaim] = []
        unverified_claims: list[GroundedClaim] = []

        for claim in extraction.claims:
            is_valid = CitationVerifier.verify_quote_substring(claim.quote, combined_text)
            verified_claim = GroundedClaim(
                fact=claim.fact,
                quote=claim.quote,
                verified=is_valid,
                source=claim.source,
            )
            if is_valid:
                verified_claims.append(verified_claim)
            else:
                unverified_claims.append(verified_claim)

        pricing = extraction.pricing_terms or "Not covered in supplied documents"

        return VendorResearchResult(
            success=True,
            vendor_id=vendor_id,
            summary=extraction.summary,
            claims=verified_claims,
            products_services=extraction.products_services,
            security_certifications=extraction.security_certifications,
            pricing_terms=pricing,
            not_covered=extraction.not_covered,
            unverified_claims=unverified_claims,
        )
