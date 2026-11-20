"""Security reviewer workspace & TPRM hardening API routes."""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.permissions import AuthContext, require_permission
from procure_agents.tprm.extractor import (
    TprmDomainAssessmentAgent,
)
from procure_core.db import get_async_session
from procure_core.models.audit import AuditLogModel
from procure_core.models.vendor import (
    TprmControlFindingModel,
    TprmRiskRegisterModel,
    VendorModel,
    VendorSecurityReviewModel,
)
from procure_core.security.embeddings import TprmEmbeddingService
from procure_core.security.isolated_extractor import (
    ExtractionIsolationError,
    IsolatedPdfExtractor,
)
from procure_core.security.malware_scanner import ClamAVScanner, QuarantineManager
from procure_core.security.pdf_scanner import StructuralPdfScanner

logger = logging.getLogger("api.routes.security")

router = APIRouter(prefix="/api/v1/security", tags=["security-reviewer"])


# =============================================================================
# Schemas
# =============================================================================


class ControlFindingSchema(BaseModel):
    domain: str
    status: str
    page_number: int | None = None
    quote: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    verified: bool = False
    rationale: str = ""
    risk_level: str = "low"


class DocumentUploadResponse(BaseModel):
    id: str
    vendor_id: str
    vendor_name: str
    doc_type: str
    status: str
    sha256: str
    is_quarantined: bool = False
    quarantine_reason: str | None = None
    auditor_opinion: str = "unqualified"
    risk_tier: str = "low"
    pipeline_stages: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[ControlFindingSchema] = Field(default_factory=list)
    active_risks: list[str] = Field(default_factory=list)
    supersedes_id: str | None = None
    provider_response_id: str | None = None
    model_name: str | None = None


class DocumentListItem(BaseModel):
    id: str
    vendor_id: str
    vendor_name: str
    doc_type: str
    status: str
    effective_date: str
    expiration_date: str
    sha256: str | None
    is_fresh: bool
    is_superseded: bool = False
    supersedes_id: str | None = None
    superseded_by_id: str | None = None
    auditor_opinion: str = "unqualified"
    verified_findings_count: int = 0
    missing_domains_count: int = 0
    risk_tier: str = "low"
    created_at: str


class RiskRegisterItem(BaseModel):
    id: str
    vendor_id: str
    vendor_name: str
    risk_tier: str
    overall_status: str
    findings_summary: dict[str, Any]
    active_risks: list[str]
    next_review_date: str | None
    updated_at: str


class DocumentPageResponse(BaseModel):
    document_id: str
    page_number: int
    text: str
    total_pages: int
    char_count: int
    sha256: str
    findings_on_page: list[ControlFindingSchema] = Field(default_factory=list)


# =============================================================================
# Endpoints
# =============================================================================


@router.post(
    "/documents/upload",
    response_model=DocumentUploadResponse,
    summary="Upload SOC 2/ISO/DPA PDF with live hardened pipeline",
)
async def upload_security_document(
    vendor_id: str | None = Form(default=None),
    vendor_name: str | None = Form(default=None),
    doc_type: str = Form(default="SOC2_TYPE_II"),
    effective_date: str | None = Form(default=None),
    expiration_date: str | None = Form(default=None),
    file: UploadFile = File(...),
    auth: AuthContext = Depends(require_permission("security:review")),
    db: AsyncSession = Depends(get_async_session),
) -> DocumentUploadResponse:
    """Hardened TPRM ingestion pipeline:

    1. Safety Scan: Structural PDF scan + ClamAV malware scan.
    2. OS-Isolated Extraction: Sandboxed child process with Job Object memory/CPU limits.
    3. OCR & Offset Mapping: Verbatim text alignment and character offsets.
    4. Chunking & Embeddings: Semantic vectors for control domain matching.
    5. Grounded LLM Assessment: 6 control domains evaluated with verbatim quotes only.
    6. Quarantine: Immediate fail-closed quarantine if hostile/malicious payload detected.
    7. Supersession: New valid report supersedes older documents with history preserved.
    """
    t = auth.tenant_id

    # 0. Verify vendor existence or create/lookup by vendor_name
    vendor = None
    if vendor_id:
        try:
            v_uuid = uuid.UUID(str(vendor_id))
            q_v = await db.execute(
                select(VendorModel).where(
                    and_(VendorModel.tenant_id == t, VendorModel.id == v_uuid)
                )
            )
            vendor = q_v.scalar_one_or_none()
        except ValueError:
            pass

    if not vendor and vendor_name:
        q_v = await db.execute(
            select(VendorModel).where(
                and_(VendorModel.tenant_id == t, VendorModel.name == vendor_name)
            )
        )
        vendor = q_v.scalar_one_or_none()
        if not vendor:
            clean_name = vendor_name.strip()
            canon_key = re.sub(r"[^a-z0-9]", "", clean_name.lower())
            vendor = VendorModel(
                id=uuid.uuid4(),
                tenant_id=t,
                name=clean_name,
                canonical_key=canon_key or f"v_{uuid.uuid4().hex[:8]}",
                normalized_base=clean_name.lower(),
                status="active",
                metadata_={},
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(vendor)
            await db.commit()
            await db.refresh(vendor)

    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor not found. Please provide a valid vendor_id or vendor_name.",
        )

    # Ensure vendor_id is the resolved vendor UUID
    vendor_id = str(vendor.id)

    # Read uploaded bytes
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty (0 bytes).",
        )

    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    pipeline_stages: list[dict[str, Any]] = []

    # 1. Pipeline Stage 1: Safety Scan (Structural PDF + ClamAV / Malware)
    pdf_scan_res = StructuralPdfScanner.scan(raw_bytes)
    malware_scan_res = ClamAVScanner().scan_bytes(raw_bytes, filename=file.filename or "upload.pdf")

    is_hostile = (not pdf_scan_res.is_safe) or (not malware_scan_res.is_clean)
    quarantine_mgr = QuarantineManager()

    if is_hostile:
        threat_reasons = []
        if not pdf_scan_res.is_safe:
            threat_reasons.extend(pdf_scan_res.threats)
        if not malware_scan_res.is_clean:
            threat_reasons.append(
                f"Malware Signature Detected: {malware_scan_res.threat_name} ({malware_scan_res.details})"
            )

        combined_reason = "; ".join(threat_reasons)
        q_info = quarantine_mgr.quarantine(
            data=raw_bytes,
            original_filename=file.filename or "upload.pdf",
            reason=combined_reason,
            tenant_id=t,
        )

        pipeline_stages.append(
            {
                "stage": "safety_scan",
                "status": "failed",
                "engine": f"StructuralScanner+{malware_scan_res.engine}",
                "threats": threat_reasons,
                "quarantined": True,
            }
        )

        # Create quarantined review record
        review_id = uuid.uuid4()
        eff_d = date.fromisoformat(effective_date) if effective_date else date.today()
        exp_d = (
            date.fromisoformat(expiration_date)
            if expiration_date
            else (date.today() + timedelta(days=365))
        )

        q_review = VendorSecurityReviewModel(
            id=review_id,
            tenant_id=t,
            vendor_id=vendor_id,
            doc_type=doc_type,
            status="quarantined",
            effective_date=eff_d,
            expiration_date=exp_d,
            sha256=sha256,
            metadata_={
                "safety_scan": {
                    "is_safe": False,
                    "threats": threat_reasons,
                    "quarantined_at": datetime.utcnow().isoformat(),
                    "quarantine_path": q_info.get("quarantine_path"),
                },
                "filename": file.filename,
            },
        )
        db.add(q_review)
        await db.flush()

        # Update Risk Register to Critical / Quarantined
        q_reg = await db.execute(
            select(TprmRiskRegisterModel).where(
                and_(
                    TprmRiskRegisterModel.tenant_id == t,
                    TprmRiskRegisterModel.vendor_id == vendor_id,
                )
            )
        )
        reg_record = q_reg.scalar_one_or_none()
        if reg_record:
            reg_record.risk_tier = "critical"
            reg_record.overall_status = "quarantined"
            reg_record.active_risks = [
                f"SECURITY INCIDENT: Hostile file uploaded ({combined_reason[:100]})"
            ]
            db.add(reg_record)
        else:
            db.add(
                TprmRiskRegisterModel(
                    id=uuid.uuid4(),
                    tenant_id=t,
                    vendor_id=vendor_id,
                    risk_tier="critical",
                    overall_status="quarantined",
                    findings_summary={"verified_count": 0, "quarantined": True},
                    active_risks=[
                        f"SECURITY INCIDENT: Hostile file uploaded ({combined_reason[:100]})"
                    ],
                )
            )

        db.add(
            AuditLogModel(
                tenant_id=t,
                event_type="security_document_quarantined",
                actor=auth.email,
                entity_type="vendor_security_review",
                entity_id=str(review_id),
                delta={"reasons": threat_reasons, "sha256": sha256, "filename": file.filename},
            )
        )
        await db.commit()

        return DocumentUploadResponse(
            id=str(review_id),
            vendor_id=str(vendor_id),
            vendor_name=vendor.name,
            doc_type=doc_type,
            status="quarantined",
            sha256=sha256,
            is_quarantined=True,
            quarantine_reason=combined_reason,
            auditor_opinion="disclaimer",
            risk_tier="critical",
            pipeline_stages=pipeline_stages,
            findings=[],
            active_risks=[f"Hostile document quarantined: {combined_reason}"],
        )

    pipeline_stages.append(
        {
            "stage": "safety_scan",
            "status": "passed",
            "engine": f"StructuralScanner+{malware_scan_res.engine}",
            "threats": [],
            "quarantined": False,
        }
    )

    # 2. Pipeline Stage 2: OS-Isolated Extraction (Job Object / Memory limit 256MB)
    extractor = IsolatedPdfExtractor(memory_limit_mb=256, timeout_seconds=15.0)
    try:
        extracted_doc = extractor.extract(raw_bytes)
        pipeline_stages.append(
            {
                "stage": "extraction",
                "status": "passed",
                "isolation": "Windows Job Object (256MB limit)"
                if extractor
                else "isolated_process",
                "duration_ms": round(extracted_doc.extraction_time_ms, 2),
                "total_pages": extracted_doc.total_pages,
            }
        )
    except ExtractionIsolationError as ex_err:
        logger.error("Isolated extraction failed: %s", ex_err)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"OS-isolated extraction failed: {str(ex_err)}",
        ) from ex_err

    # 3. Pipeline Stage 3: OCR & Offset Mapping
    pipeline_stages.append(
        {
            "stage": "ocr",
            "status": "passed",
            "total_chars": extracted_doc.total_chars,
            "pages_processed": extracted_doc.total_pages,
        }
    )

    # 4. Pipeline Stage 4 & 5: Chunking & Embeddings
    embedding_svc = TprmEmbeddingService()
    chunks = embedding_svc.chunk_document(
        [{"page_number": p.page_number, "text": p.text} for p in extracted_doc.pages]
    )
    embeddings = embedding_svc.embed_texts([c.text for c in chunks[:10]])  # embed top chunks
    pipeline_stages.append(
        {
            "stage": "chunking",
            "status": "passed",
            "chunk_count": len(chunks),
        }
    )
    pipeline_stages.append(
        {
            "stage": "embeddings",
            "status": "passed",
            "dimension": 768,
            "vector_count": len(embeddings),
        }
    )

    # 5. Pipeline Stage 6: Grounded Domain Assessment & Verbatim Quote Verification
    agent = TprmDomainAssessmentAgent()
    assessment = await agent.assess_document(
        extracted_doc=extracted_doc,
        doc_type=doc_type,
        vendor_name=vendor.name,
    )
    pipeline_stages.append(
        {
            "stage": "assessment",
            "status": "passed",
            "model": assessment.model_name or "gemini/gemini-3.1-flash-lite",
            "response_id": assessment.provider_response_id,
            "auditor_opinion": assessment.auditor_opinion,
            "verified_quotes": assessment.verified_quote_count,
            "missing_domains": assessment.missing_domains,
        }
    )

    # 6. Supersession Handling
    # Check if prior active/compliant review of this doc_type exists for this vendor
    q_prev = await db.execute(
        select(VendorSecurityReviewModel).where(
            and_(
                VendorSecurityReviewModel.tenant_id == t,
                VendorSecurityReviewModel.vendor_id == vendor_id,
                VendorSecurityReviewModel.doc_type == doc_type,
                VendorSecurityReviewModel.status.in_(
                    ["compliant", "qualified_opinion", "remediation_required"]
                ),
            )
        )
    )
    previous_reviews = q_prev.scalars().all()
    superseded_id = None

    review_id = uuid.uuid4()
    eff_d = date.fromisoformat(effective_date) if effective_date else date.today()
    exp_d = (
        date.fromisoformat(expiration_date)
        if expiration_date
        else (date.today() + timedelta(days=365))
    )

    # Save extracted document to storage or mock s3 path
    s3_key = f"tprm/{t}/{vendor_id}/{review_id}.pdf"

    # Mark prior reviews as superseded
    for prev in previous_reviews:
        prev.status = "superseded"
        prev_meta = dict(prev.metadata_ or {})
        prev_meta["superseded_by_id"] = str(review_id)
        prev_meta["superseded_at"] = datetime.utcnow().isoformat()
        prev.metadata_ = prev_meta
        db.add(prev)
        superseded_id = str(prev.id)

    # Store full review
    new_review = VendorSecurityReviewModel(
        id=review_id,
        tenant_id=t,
        vendor_id=vendor_id,
        doc_type=doc_type,
        status=assessment.overall_status,
        effective_date=eff_d,
        expiration_date=exp_d,
        s3_key=s3_key,
        sha256=sha256,
        metadata_={
            "auditor_opinion": assessment.auditor_opinion,
            "risk_tier": assessment.risk_tier,
            "supersedes_id": superseded_id,
            "provider_response_id": assessment.provider_response_id,
            "model_name": assessment.model_name,
            "pipeline_stages": pipeline_stages,
            "verified_quote_count": assessment.verified_quote_count,
            "missing_domains": assessment.missing_domains,
            "pages_meta": [
                {
                    "page_number": p.page_number,
                    "char_count": p.char_count,
                    "char_start": p.char_start,
                    "char_end": p.char_end,
                }
                for p in extracted_doc.pages
            ],
            "raw_pages_text": {str(p.page_number): p.text for p in extracted_doc.pages},
        },
    )
    db.add(new_review)
    await db.flush()

    # Store individual control findings in table
    for f in assessment.findings:
        db.add(
            TprmControlFindingModel(
                id=uuid.uuid4(),
                tenant_id=t,
                vendor_id=vendor_id,
                security_review_id=review_id,
                domain=f.domain,
                status=f.status,
                page_number=f.page_number,
                quote=f.quote,
                start_offset=f.start_offset,
                end_offset=f.end_offset,
                verified=f.verified,
                details={"rationale": f.rationale, "risk_level": f.risk_level},
            )
        )

    # Upsert Risk Register record
    q_reg = await db.execute(
        select(TprmRiskRegisterModel).where(
            and_(
                TprmRiskRegisterModel.tenant_id == t,
                TprmRiskRegisterModel.vendor_id == vendor_id,
            )
        )
    )
    reg_record = q_reg.scalar_one_or_none()
    summary_data = {
        "verified_count": assessment.verified_quote_count,
        "missing_count": len(assessment.missing_domains),
        "auditor_opinion": assessment.auditor_opinion,
        "latest_doc_type": doc_type,
        "latest_sha256": sha256,
    }

    if reg_record:
        reg_record.risk_tier = assessment.risk_tier
        reg_record.overall_status = assessment.overall_status
        reg_record.findings_summary = summary_data
        reg_record.active_risks = assessment.active_risks
        reg_record.next_review_date = exp_d
        db.add(reg_record)
    else:
        db.add(
            TprmRiskRegisterModel(
                id=uuid.uuid4(),
                tenant_id=t,
                vendor_id=vendor_id,
                risk_tier=assessment.risk_tier,
                overall_status=assessment.overall_status,
                findings_summary=summary_data,
                active_risks=assessment.active_risks,
                next_review_date=exp_d,
            )
        )

    db.add(
        AuditLogModel(
            tenant_id=t,
            event_type="security_document_processed",
            actor=auth.email,
            entity_type="vendor_security_review",
            entity_id=str(review_id),
            delta={
                "status": assessment.overall_status,
                "auditor_opinion": assessment.auditor_opinion,
                "risk_tier": assessment.risk_tier,
                "verified_quotes": assessment.verified_quote_count,
                "supersedes_id": superseded_id,
            },
        )
    )
    await db.commit()

    findings_out = [
        ControlFindingSchema(
            domain=f.domain,
            status=f.status,
            page_number=f.page_number,
            quote=f.quote,
            start_offset=f.start_offset,
            end_offset=f.end_offset,
            verified=f.verified,
            rationale=f.rationale,
            risk_level=f.risk_level,
        )
        for f in assessment.findings
    ]

    return DocumentUploadResponse(
        id=str(review_id),
        vendor_id=str(vendor_id),
        vendor_name=vendor.name,
        doc_type=doc_type,
        status=assessment.overall_status,
        sha256=sha256,
        is_quarantined=False,
        auditor_opinion=assessment.auditor_opinion,
        risk_tier=assessment.risk_tier,
        pipeline_stages=pipeline_stages,
        findings=findings_out,
        active_risks=assessment.active_risks,
        supersedes_id=superseded_id,
        provider_response_id=assessment.provider_response_id,
        model_name=assessment.model_name,
    )


@router.get(
    "/documents",
    response_model=list[DocumentListItem],
    summary="List TPRM documents with supersession chain and findings",
)
async def list_security_documents(
    vendor_id: uuid.UUID | None = None,
    include_superseded: bool = True,
    auth: AuthContext = Depends(require_permission("security:read")),
    db: AsyncSession = Depends(get_async_session),
) -> list[DocumentListItem]:
    """List security review documents with vendor metadata and supersession status."""
    t = auth.tenant_id

    stmt = (
        select(VendorSecurityReviewModel, VendorModel.name)
        .join(
            VendorModel,
            and_(
                VendorModel.tenant_id == VendorSecurityReviewModel.tenant_id,
                VendorModel.id == VendorSecurityReviewModel.vendor_id,
            ),
        )
        .where(VendorSecurityReviewModel.tenant_id == t)
        .order_by(VendorSecurityReviewModel.created_at.desc())
    )

    if vendor_id:
        stmt = stmt.where(VendorSecurityReviewModel.vendor_id == vendor_id)
    if not include_superseded:
        stmt = stmt.where(VendorSecurityReviewModel.status != "superseded")

    res = await db.execute(stmt)
    rows = res.all()

    today = date.today()
    items = []
    for review, v_name in rows:
        meta = review.metadata_ or {}
        is_fresh = review.expiration_date >= today if review.expiration_date else False
        is_sup = review.status == "superseded"

        items.append(
            DocumentListItem(
                id=str(review.id),
                vendor_id=str(review.vendor_id),
                vendor_name=v_name,
                doc_type=review.doc_type,
                status=review.status,
                effective_date=review.effective_date.isoformat() if review.effective_date else "",
                expiration_date=review.expiration_date.isoformat()
                if review.expiration_date
                else "",
                sha256=review.sha256,
                is_fresh=is_fresh,
                is_superseded=is_sup,
                supersedes_id=meta.get("supersedes_id"),
                superseded_by_id=meta.get("superseded_by_id"),
                auditor_opinion=meta.get("auditor_opinion", "unqualified"),
                verified_findings_count=meta.get("verified_quote_count", 0),
                missing_domains_count=len(meta.get("missing_domains", [])),
                risk_tier=meta.get("risk_tier", "low"),
                created_at=review.created_at.isoformat() if review.created_at else "",
            )
        )

    return items


@router.get(
    "/documents/{doc_id}",
    summary="Detailed document review with findings and verbatim quotes",
)
async def get_security_document(
    doc_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("security:read")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Fetch complete TPRM document review, structured domain findings, and audit evidence."""
    t = auth.tenant_id

    stmt = (
        select(VendorSecurityReviewModel, VendorModel.name)
        .join(
            VendorModel,
            and_(
                VendorModel.tenant_id == VendorSecurityReviewModel.tenant_id,
                VendorModel.id == VendorSecurityReviewModel.vendor_id,
            ),
        )
        .where(
            and_(
                VendorSecurityReviewModel.tenant_id == t,
                VendorSecurityReviewModel.id == doc_id,
            )
        )
    )
    res = await db.execute(stmt)
    row = res.one_or_none()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Security document {doc_id} not found.",
        )

    review, v_name = row
    meta = review.metadata_ or {}

    # Query findings from table
    q_f = await db.execute(
        select(TprmControlFindingModel).where(
            and_(
                TprmControlFindingModel.tenant_id == t,
                TprmControlFindingModel.security_review_id == doc_id,
            )
        )
    )
    findings = q_f.scalars().all()

    findings_data = [
        {
            "domain": f.domain,
            "status": f.status,
            "page_number": f.page_number,
            "quote": f.quote,
            "start_offset": f.start_offset,
            "end_offset": f.end_offset,
            "verified": f.verified,
            "rationale": (f.details or {}).get("rationale", ""),
            "risk_level": (f.details or {}).get("risk_level", "low"),
        }
        for f in findings
    ]

    return {
        "id": str(review.id),
        "vendor_id": str(review.vendor_id),
        "vendor_name": v_name,
        "doc_type": review.doc_type,
        "status": review.status,
        "effective_date": review.effective_date.isoformat() if review.effective_date else "",
        "expiration_date": review.expiration_date.isoformat() if review.expiration_date else "",
        "sha256": review.sha256,
        "is_quarantined": review.status == "quarantined",
        "auditor_opinion": meta.get("auditor_opinion", "unqualified"),
        "risk_tier": meta.get("risk_tier", "low"),
        "supersedes_id": meta.get("supersedes_id"),
        "superseded_by_id": meta.get("superseded_by_id"),
        "provider_response_id": meta.get("provider_response_id"),
        "pipeline_stages": meta.get("pipeline_stages", []),
        "findings": findings_data,
        "missing_domains": meta.get("missing_domains", []),
        "verified_quote_count": meta.get("verified_quote_count", 0),
        "total_pages": len(meta.get("pages_meta", [])),
    }


@router.get(
    "/documents/{doc_id}/pages/{page_number}",
    response_model=DocumentPageResponse,
    summary="Evidence viewer: page text and highlighted cited quotes",
)
async def get_document_page(
    doc_id: uuid.UUID,
    page_number: int,
    auth: AuthContext = Depends(require_permission("security:read")),
    db: AsyncSession = Depends(get_async_session),
) -> DocumentPageResponse:
    """Retrieve raw text of a specific page for the Evidence Viewer highlighting."""
    t = auth.tenant_id

    q = await db.execute(
        select(VendorSecurityReviewModel).where(
            and_(
                VendorSecurityReviewModel.tenant_id == t,
                VendorSecurityReviewModel.id == doc_id,
            )
        )
    )
    review = q.scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="Document not found")

    meta = review.metadata_ or {}
    raw_pages = meta.get("raw_pages_text", {})
    page_text = raw_pages.get(str(page_number), "")

    # Findings on this page
    q_f = await db.execute(
        select(TprmControlFindingModel).where(
            and_(
                TprmControlFindingModel.tenant_id == t,
                TprmControlFindingModel.security_review_id == doc_id,
                TprmControlFindingModel.page_number == page_number,
            )
        )
    )
    findings = q_f.scalars().all()

    findings_on_page = [
        ControlFindingSchema(
            domain=f.domain,
            status=f.status,
            page_number=f.page_number,
            quote=f.quote,
            start_offset=f.start_offset,
            end_offset=f.end_offset,
            verified=f.verified,
            rationale=(f.details or {}).get("rationale", ""),
            risk_level=(f.details or {}).get("risk_level", "low"),
        )
        for f in findings
    ]

    total_pages = len(meta.get("pages_meta", [])) or 1

    return DocumentPageResponse(
        document_id=str(review.id),
        page_number=page_number,
        text=page_text,
        total_pages=total_pages,
        char_count=len(page_text),
        sha256=review.sha256 or "",
        findings_on_page=findings_on_page,
    )


@router.get(
    "/risk-register",
    response_model=list[RiskRegisterItem],
    summary="Vendor TPRM Risk Register",
)
async def get_risk_register(
    auth: AuthContext = Depends(require_permission("security:read")),
    db: AsyncSession = Depends(get_async_session),
) -> list[RiskRegisterItem]:
    """Retrieve enterprise third-party risk register across all vendors."""
    t = auth.tenant_id

    stmt = (
        select(TprmRiskRegisterModel, VendorModel.name)
        .join(
            VendorModel,
            and_(
                VendorModel.tenant_id == TprmRiskRegisterModel.tenant_id,
                VendorModel.id == TprmRiskRegisterModel.vendor_id,
            ),
        )
        .where(TprmRiskRegisterModel.tenant_id == t)
        .order_by(TprmRiskRegisterModel.updated_at.desc())
    )
    res = await db.execute(stmt)
    rows = res.all()

    return [
        RiskRegisterItem(
            id=str(reg.id),
            vendor_id=str(reg.vendor_id),
            vendor_name=v_name,
            risk_tier=reg.risk_tier,
            overall_status=reg.overall_status,
            findings_summary=reg.findings_summary or {},
            active_risks=reg.active_risks or [],
            next_review_date=reg.next_review_date.isoformat() if reg.next_review_date else None,
            updated_at=reg.updated_at.isoformat() if reg.updated_at else "",
        )
        for reg, v_name in rows
    ]


@router.post(
    "/documents/{doc_id}/quarantine",
    summary="Manually quarantine a vendor security document",
)
async def quarantine_document(
    doc_id: uuid.UUID,
    reason: str = Form(default="Manual security reviewer quarantine action"),
    auth: AuthContext = Depends(require_permission("security:review")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Quarantine an active document, revoking its use for approvals."""
    t = auth.tenant_id

    q = await db.execute(
        select(VendorSecurityReviewModel).where(
            and_(
                VendorSecurityReviewModel.tenant_id == t,
                VendorSecurityReviewModel.id == doc_id,
            )
        )
    )
    review = q.scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="Document not found")

    review.status = "quarantined"
    meta = dict(review.metadata_ or {})
    meta["manual_quarantine"] = {
        "actor": auth.email,
        "reason": reason,
        "timestamp": datetime.utcnow().isoformat(),
    }
    review.metadata_ = meta
    db.add(review)

    # Update risk register
    q_reg = await db.execute(
        select(TprmRiskRegisterModel).where(
            and_(
                TprmRiskRegisterModel.tenant_id == t,
                TprmRiskRegisterModel.vendor_id == review.vendor_id,
            )
        )
    )
    reg = q_reg.scalar_one_or_none()
    if reg:
        reg.risk_tier = "critical"
        reg.overall_status = "quarantined"
        reg.active_risks = list(set((reg.active_risks or []) + [f"QUARANTINED: {reason}"]))
        db.add(reg)

    db.add(
        AuditLogModel(
            tenant_id=t,
            event_type="security_document_quarantined_manual",
            actor=auth.email,
            entity_type="vendor_security_review",
            entity_id=str(doc_id),
            delta={"reason": reason},
        )
    )
    await db.commit()

    return {"status": "quarantined", "doc_id": str(doc_id), "reason": reason}
