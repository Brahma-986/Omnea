"""Legal API routes: contract repository, terms extraction, flagged clauses, and audit logs."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.permissions import AuthContext, require_permission
from procure_core.db import get_async_session
from procure_core.models.audit import AuditLogModel
from procure_core.models.vendor import ContractModel, VendorModel

router = APIRouter(prefix="/api/v1/legal", tags=["Legal"])


# =============================================================================
# Schemas
# =============================================================================


class FlaggedClause(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    quote: str
    page: int | str | None = None
    clause_type: str  # e.g. "unlimited_liability", "auto_renewal", "governing_law"
    risk_level: str  # "low", "medium", "high", "critical"
    recommendation: str
    created_at: str | None = None


class ContractListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    vendor_id: uuid.UUID
    vendor_name: str
    status: str
    start_date: str
    end_date: str | None
    total_value: float
    currency: str
    auto_renew: bool
    renewal_terms: str | None = None
    msa_status: str | None = None
    dpa_status: str | None = None
    created_at: str


class ContractDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    vendor_id: uuid.UUID
    vendor_name: str
    status: str
    start_date: str
    end_date: str | None
    total_value: float
    currency: str
    auto_renew: bool
    renewal_terms: str | None = None
    msa_status: str | None = None
    dpa_status: str | None = None
    flagged_clauses: list[FlaggedClause] = Field(default_factory=list)
    renewal_notice_terms: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    audit_details: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
    updated_at: str


class IngestContractRequest(BaseModel):
    vendor_id: uuid.UUID
    title: str
    start_date: date
    end_date: date | None = None
    total_value: Decimal
    currency: str
    auto_renew: bool = False
    status: str = "active"
    msa_status: str | None = None
    dpa_status: str | None = None
    renewal_terms: str | None = None
    flagged_clauses: list[FlaggedClause] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FlagClauseRequest(BaseModel):
    quote: str
    page: int | str | None = None
    clause_type: str
    risk_level: str  # "low", "medium", "high", "critical"
    recommendation: str


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/contracts", response_model=list[ContractListItem], summary="List legal contracts")
async def list_contracts(
    status_filter: str | None = Query(None, alias="status"),
    msa_status: str | None = Query(None),
    dpa_status: str | None = Query(None),
    vendor_id: uuid.UUID | None = Query(None),
    auth: AuthContext = Depends(require_permission("legal:read")),
    db: AsyncSession = Depends(get_async_session),
) -> list[ContractListItem]:
    """List contracts with legal filters: status, msa_status, dpa_status, vendor_id."""
    t = auth.tenant_id

    stmt = (
        select(ContractModel, VendorModel.name.label("vendor_name"))
        .join(
            VendorModel,
            and_(
                VendorModel.tenant_id == ContractModel.tenant_id,
                VendorModel.id == ContractModel.vendor_id,
            ),
        )
        .where(ContractModel.tenant_id == t)
    )

    if status_filter:
        stmt = stmt.where(ContractModel.status == status_filter)
    if vendor_id:
        stmt = stmt.where(ContractModel.vendor_id == vendor_id)

    stmt = stmt.order_by(ContractModel.created_at.desc())

    res = await db.execute(stmt)
    rows = res.all()

    results: list[ContractListItem] = []
    for contract, v_name in rows:
        meta: dict[str, Any] = contract.metadata_ or {}
        row_msa = meta.get("msa_status")
        row_dpa = meta.get("dpa_status")
        renewal = meta.get("renewal_terms")

        # In-memory filter on metadata if requested
        if msa_status and row_msa != msa_status:
            continue
        if dpa_status and row_dpa != dpa_status:
            continue

        results.append(
            ContractListItem(
                id=contract.id,
                title=contract.title,
                vendor_id=contract.vendor_id,
                vendor_name=v_name,
                status=contract.status,
                start_date=str(contract.start_date),
                end_date=str(contract.end_date) if contract.end_date else None,
                total_value=float(contract.total_value),
                currency=contract.currency,
                auto_renew=contract.auto_renew,
                renewal_terms=renewal,
                msa_status=row_msa,
                dpa_status=row_dpa,
                created_at=contract.created_at.isoformat(),
            )
        )

    return results


@router.get("/contracts/{id}", response_model=ContractDetailResponse, summary="Get contract detail")
async def get_contract_detail(
    id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("legal:read")),
    db: AsyncSession = Depends(get_async_session),
) -> ContractDetailResponse:
    """Detailed contract view with flagged clauses, renewal terms, and audit history."""
    t = auth.tenant_id

    stmt = (
        select(ContractModel, VendorModel.name.label("vendor_name"))
        .join(
            VendorModel,
            and_(
                VendorModel.tenant_id == ContractModel.tenant_id,
                VendorModel.id == ContractModel.vendor_id,
            ),
        )
        .where(and_(ContractModel.tenant_id == t, ContractModel.id == id))
    )
    res = await db.execute(stmt)
    row = res.first()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contract '{id}' not found",
        )

    contract, v_name = row
    meta: dict[str, Any] = contract.metadata_ or {}

    raw_clauses = meta.get("flagged_clauses", [])
    flagged_clauses = [
        FlaggedClause(
            id=c.get("id", str(uuid.uuid4())),
            quote=c["quote"],
            page=c.get("page"),
            clause_type=c.get("clause_type", "general"),
            risk_level=c.get("risk_level", "medium"),
            recommendation=c.get("recommendation", ""),
            created_at=c.get("created_at"),
        )
        for c in raw_clauses
    ]

    renewal_terms = meta.get("renewal_terms")
    renewal_notice = meta.get("renewal_notice_terms") or renewal_terms

    # Fetch audit log for this contract
    q_audit = await db.execute(
        select(AuditLogModel)
        .where(
            and_(
                AuditLogModel.tenant_id == t,
                AuditLogModel.entity_type.in_(["contract", "contracts"]),
                AuditLogModel.entity_id == str(id),
            )
        )
        .order_by(AuditLogModel.created_at.desc())
    )
    audit_rows = [
        {
            "id": str(a.id),
            "event_type": a.event_type,
            "actor": a.actor,
            "delta": a.delta,
            "created_at": a.created_at.isoformat(),
        }
        for a in q_audit.scalars().all()
    ]

    return ContractDetailResponse(
        id=contract.id,
        title=contract.title,
        vendor_id=contract.vendor_id,
        vendor_name=v_name,
        status=contract.status,
        start_date=str(contract.start_date),
        end_date=str(contract.end_date) if contract.end_date else None,
        total_value=float(contract.total_value),
        currency=contract.currency,
        auto_renew=contract.auto_renew,
        renewal_terms=renewal_terms,
        msa_status=meta.get("msa_status"),
        dpa_status=meta.get("dpa_status"),
        flagged_clauses=flagged_clauses,
        renewal_notice_terms=renewal_notice,
        metadata=meta,
        audit_details=audit_rows,
        created_at=contract.created_at.isoformat(),
        updated_at=contract.updated_at.isoformat(),
    )


@router.post("/contracts", response_model=ContractDetailResponse, summary="Ingest contract")
async def ingest_contract(
    payload: IngestContractRequest,
    auth: AuthContext = Depends(require_permission("legal:manage")),
    db: AsyncSession = Depends(get_async_session),
) -> ContractDetailResponse:
    """Ingest contract with metadata, flagged clauses, and renewal terms."""
    t = auth.tenant_id

    # Verify vendor exists in tenant
    q_v = await db.execute(
        select(VendorModel).where(
            and_(VendorModel.tenant_id == t, VendorModel.id == payload.vendor_id)
        )
    )
    vendor = q_v.scalar_one_or_none()
    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Vendor '{payload.vendor_id}' not found in tenant '{t}'",
        )

    # Prepare metadata
    meta = dict(payload.metadata)
    if payload.msa_status:
        meta["msa_status"] = payload.msa_status
    if payload.dpa_status:
        meta["dpa_status"] = payload.dpa_status
    if payload.renewal_terms:
        meta["renewal_terms"] = payload.renewal_terms
        meta["renewal_notice_terms"] = payload.renewal_terms
    if payload.flagged_clauses:
        meta["flagged_clauses"] = [c.model_dump(mode="json") for c in payload.flagged_clauses]

    contract_id = uuid.uuid4()
    contract = ContractModel(
        id=contract_id,
        tenant_id=t,
        vendor_id=payload.vendor_id,
        title=payload.title,
        status=payload.status,
        start_date=payload.start_date,
        end_date=payload.end_date,
        total_value=payload.total_value,
        currency=payload.currency.upper(),
        auto_renew=payload.auto_renew,
        metadata_=meta,
    )
    db.add(contract)

    audit = AuditLogModel(
        tenant_id=t,
        event_type="contract_ingested",
        actor=auth.email,
        entity_type="contract",
        entity_id=str(contract_id),
        delta={
            "title": payload.title,
            "vendor_id": str(payload.vendor_id),
            "total_value": float(payload.total_value),
            "currency": payload.currency.upper(),
        },
    )
    db.add(audit)

    await db.commit()
    await db.refresh(contract)

    return await get_contract_detail(id=contract.id, auth=auth, db=db)


@router.post(
    "/contracts/{id}/flag-clause",
    response_model=list[FlaggedClause],
    summary="Flag clause in contract",
)
async def flag_clause(
    id: uuid.UUID,
    payload: FlagClauseRequest,
    auth: AuthContext = Depends(require_permission("legal:manage")),
    db: AsyncSession = Depends(get_async_session),
) -> list[FlaggedClause]:
    """Add or update a flagged clause on a contract."""
    t = auth.tenant_id

    q = await db.execute(
        select(ContractModel).where(and_(ContractModel.tenant_id == t, ContractModel.id == id))
    )
    contract = q.scalar_one_or_none()
    if not contract:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contract '{id}' not found",
        )

    meta = dict(contract.metadata_ or {})
    clauses = list(meta.get("flagged_clauses", []))

    new_clause = FlaggedClause(
        id=str(uuid.uuid4()),
        quote=payload.quote,
        page=payload.page,
        clause_type=payload.clause_type,
        risk_level=payload.risk_level,
        recommendation=payload.recommendation,
    )
    clauses.append(new_clause.model_dump(mode="json"))
    meta["flagged_clauses"] = clauses
    contract.metadata_ = meta
    db.add(contract)

    audit = AuditLogModel(
        tenant_id=t,
        event_type="clause_flagged",
        actor=auth.email,
        entity_type="contract",
        entity_id=str(id),
        delta={
            "clause_type": payload.clause_type,
            "risk_level": payload.risk_level,
            "quote": payload.quote[:60],
        },
    )
    db.add(audit)

    await db.commit()
    await db.refresh(contract)

    return [
        FlaggedClause(
            id=c.get("id", str(uuid.uuid4())),
            quote=c["quote"],
            page=c.get("page"),
            clause_type=c.get("clause_type", "general"),
            risk_level=c.get("risk_level", "medium"),
            recommendation=c.get("recommendation", ""),
            created_at=c.get("created_at"),
        )
        for c in (contract.metadata_ or {}).get("flagged_clauses", [])
    ]
