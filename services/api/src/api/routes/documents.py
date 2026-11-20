"""Documents and TPRM Assessments REST API endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.permissions import AuthContext, require_permission
from procure_core.db import get_async_session
from procure_core.models.vendor import (
    TprmControlFindingModel,
    VendorSecurityReviewModel,
)

router = APIRouter(
    prefix="/api/v1/documents", tags=["Developer Platform - Documents & Assessments"]
)


class DocumentItem(BaseModel):
    id: str
    vendor_id: str
    doc_type: str
    original_filename: str
    sha256_hash: str
    pipeline_status: str
    is_quarantined: bool
    created_at: str


class AssessmentFindingItem(BaseModel):
    id: str
    domain: str
    status: str
    page_number: int | None
    quote: str | None
    is_verified: bool
    confidence_score: float | None


class DocumentAssessmentResponse(BaseModel):
    document_id: str
    pipeline_status: str
    findings: list[AssessmentFindingItem]
    overall_status: str


@router.get("", response_model=list[DocumentItem])
async def list_documents(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(require_permission("security:read")),
    db: AsyncSession = Depends(get_async_session),
) -> list[dict[str, Any]]:
    """List documents and third-party security reviews."""
    stmt = (
        select(VendorSecurityReviewModel)
        .where(VendorSecurityReviewModel.tenant_id == auth.tenant_id)
        .order_by(VendorSecurityReviewModel.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    res = await db.execute(stmt)
    docs = res.scalars().all()

    return [
        {
            "id": str(d.id),
            "vendor_id": str(d.vendor_id),
            "doc_type": d.doc_type,
            "original_filename": d.original_filename,
            "sha256_hash": d.sha256_hash,
            "pipeline_status": d.pipeline_status,
            "is_quarantined": d.is_quarantined,
            "created_at": d.created_at.isoformat()
            if hasattr(d, "created_at") and d.created_at
            else "",
        }
        for d in docs
    ]


@router.get("/{doc_id}", response_model=DocumentItem)
async def get_document(
    doc_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("security:read")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Retrieve document metadata and extraction pipeline status."""
    stmt = select(VendorSecurityReviewModel).where(
        VendorSecurityReviewModel.id == doc_id,
        VendorSecurityReviewModel.tenant_id == auth.tenant_id,
    )
    res = await db.execute(stmt)
    d = res.scalar_one_or_none()
    if not d:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    return {
        "id": str(d.id),
        "vendor_id": str(d.vendor_id),
        "doc_type": d.doc_type,
        "original_filename": d.original_filename,
        "sha256_hash": d.sha256_hash,
        "pipeline_status": d.pipeline_status,
        "is_quarantined": d.is_quarantined,
        "created_at": d.created_at.isoformat() if hasattr(d, "created_at") and d.created_at else "",
    }


@router.get("/{doc_id}/assessment", response_model=DocumentAssessmentResponse)
async def get_document_assessment(
    doc_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("security:read")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Retrieve TPRM control domain assessment and verified findings for a document."""
    stmt = select(VendorSecurityReviewModel).where(
        VendorSecurityReviewModel.id == doc_id,
        VendorSecurityReviewModel.tenant_id == auth.tenant_id,
    )
    res = await db.execute(stmt)
    doc = res.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    findings_stmt = select(TprmControlFindingModel).where(
        TprmControlFindingModel.security_review_id == doc_id,
        TprmControlFindingModel.tenant_id == auth.tenant_id,
    )
    f_res = await db.execute(findings_stmt)
    findings = f_res.scalars().all()

    findings_list = [
        {
            "id": str(f.id),
            "domain": f.domain,
            "status": f.status,
            "page_number": f.page_number,
            "quote": f.quote,
            "is_verified": f.is_verified,
            "confidence_score": float(f.confidence_score)
            if f.confidence_score is not None
            else None,
        }
        for f in findings
    ]

    overall = (
        "approved"
        if all(f.status == "verified" for f in findings) and findings
        else doc.pipeline_status
    )

    return {
        "document_id": str(doc_id),
        "pipeline_status": doc.pipeline_status,
        "findings": findings_list,
        "overall_status": overall,
    }
