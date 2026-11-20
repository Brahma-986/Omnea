"""Vendors API routes: identity ledger, spend per currency, graph projection, merge/unmerge, and research."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.permissions import AuthContext, require_permission
from procure_agents.research.vendor_researcher import (
    VendorResearchAgent,
    VendorResearchResult,
)
from procure_context.review.manager import ReviewManager
from procure_core.db import get_async_session
from procure_core.models.audit import AuditLogModel
from procure_core.models.vendor import (
    ContractModel,
    SpendTransactionModel,
    VendorAliasModel,
    VendorModel,
    VendorSecurityReviewModel,
)

router = APIRouter(prefix="/api/v1/vendors", tags=["Vendors"])


# =============================================================================
# Response & Request Schemas
# =============================================================================


class VendorListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    canonical_key: str
    name: str
    region: str | None
    status: str
    parent_vendor_id: uuid.UUID | None
    parent_name: str | None = None
    aliases: list[str] = Field(default_factory=list)
    active_contracts_count: int = 0
    security_status: str = "none"
    spend_by_currency: dict[str, float] = Field(default_factory=dict)
    merged_into_id: uuid.UUID | None = None


class ContractItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    status: str
    start_date: str
    end_date: str | None
    total_value: float
    currency: str
    auto_renew: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


class SpendTransactionItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vendor_id: uuid.UUID
    transaction_date: str
    amount: float
    currency: str
    category: str
    source_system: str
    source_id: str


class SecurityReviewItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    doc_type: str
    status: str
    effective_date: str
    expiration_date: str


class VendorDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    canonical_key: str
    name: str
    normalized_base: str
    region: str | None
    legal_name: str | None
    status: str
    previous_status: str | None
    parent_vendor_id: uuid.UUID | None
    parent_name: str | None = None
    merged_into_id: uuid.UUID | None
    metadata: dict[str, Any] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    child_subsidiaries: list[dict[str, Any]] = Field(default_factory=list)
    contracts: list[ContractItem] = Field(default_factory=list)
    spend_transactions: list[SpendTransactionItem] = Field(default_factory=list)
    spend_by_currency: dict[str, float] = Field(default_factory=dict)
    security_reviews: list[SecurityReviewItem] = Field(default_factory=list)
    audit_history: list[dict[str, Any]] = Field(default_factory=list)


class GraphNode(BaseModel):
    id: str
    label: str  # "vendor" | "contract"
    name: str | None = None
    title: str | None = None
    status: str
    spend_by_currency: dict[str, float] = Field(default_factory=dict)
    total_value: float | None = None
    currency: str | None = None
    vendor_id: str | None = None
    region: str | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    type: str  # "SUBSIDIARY_OF" | "MERGED_INTO" | "CONTRACT_WITH"


class VendorGraphResponse(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class MergeVendorsRequest(BaseModel):
    source_id: uuid.UUID
    target_id: uuid.UUID


class ResearchVendorRequest(BaseModel):
    documents: list[str] = Field(default_factory=list)
    text: str | None = None


# =============================================================================
# Endpoints
# =============================================================================


@router.get("", response_model=list[VendorListItem], summary="List enterprise vendors")
async def list_vendors(
    auth: AuthContext = Depends(require_permission("vendors:read")),
    db: AsyncSession = Depends(get_async_session),
) -> list[VendorListItem]:
    """List vendors for the authenticated tenant with spend per currency and merged accounting."""
    t = auth.tenant_id

    # 1. Fetch vendors
    q_vendors = await db.execute(
        select(VendorModel).where(VendorModel.tenant_id == t).order_by(VendorModel.name.asc())
    )
    vendors = q_vendors.scalars().all()
    vendor_map = {v.id: v for v in vendors}

    # 2. Fetch aliases
    q_aliases = await db.execute(select(VendorAliasModel).where(VendorAliasModel.tenant_id == t))
    aliases = q_aliases.scalars().all()
    alias_map: dict[uuid.UUID, list[str]] = {}
    for a in aliases:
        alias_map.setdefault(a.vendor_id, []).append(a.alias)

    # 3. Fetch active contracts
    q_contracts = await db.execute(
        select(ContractModel).where(
            and_(ContractModel.tenant_id == t, ContractModel.status == "active")
        )
    )
    contracts = q_contracts.scalars().all()
    contract_count_map: dict[uuid.UUID, int] = {}
    for c in contracts:
        contract_count_map[c.vendor_id] = contract_count_map.get(c.vendor_id, 0) + 1

    # 4. Fetch security reviews
    q_sec = await db.execute(
        select(VendorSecurityReviewModel)
        .where(VendorSecurityReviewModel.tenant_id == t)
        .order_by(VendorSecurityReviewModel.created_at.desc())
    )
    sec_reviews = q_sec.scalars().all()
    sec_status_map: dict[uuid.UUID, str] = {}
    for sec_rev in sec_reviews:
        if sec_rev.vendor_id not in sec_status_map:
            sec_status_map[sec_rev.vendor_id] = sec_rev.status

    # 5. Fetch spend transactions & calculate spend per currency
    q_spend = await db.execute(
        select(SpendTransactionModel).where(SpendTransactionModel.tenant_id == t)
    )
    spend_rows = q_spend.scalars().all()

    # Raw spend per vendor (strictly per currency)
    raw_spend: dict[uuid.UUID, dict[str, Decimal]] = {}
    for sp_tx in spend_rows:
        raw_spend.setdefault(sp_tx.vendor_id, {})
        curr = sp_tx.currency.upper()
        raw_spend[sp_tx.vendor_id][curr] = (
            raw_spend[sp_tx.vendor_id].get(curr, Decimal("0")) + sp_tx.amount
        )

    # Aggregate spend handling merged vendors:
    # If source vendor is merged into target, target includes source's spend.
    # The merged vendor itself reflects zeroed active spend since it rolled into target.
    final_spend: dict[uuid.UUID, dict[str, Decimal]] = {
        v.id: dict(raw_spend.get(v.id, {})) for v in vendors
    }

    for v in vendors:
        if v.status == "merged" and v.merged_into_id and v.merged_into_id in final_spend:
            target_id = v.merged_into_id
            for curr, amt in raw_spend.get(v.id, {}).items():
                final_spend[target_id][curr] = final_spend[target_id].get(curr, Decimal("0")) + amt
            # Zero out merged vendor's own spend to avoid double counting
            final_spend[v.id] = {}

    items: list[VendorListItem] = []
    for v in vendors:
        parent_name = (
            vendor_map[v.parent_vendor_id].name
            if v.parent_vendor_id and v.parent_vendor_id in vendor_map
            else None
        )
        spend_dict = {curr: float(amt) for curr, amt in sorted(final_spend.get(v.id, {}).items())}
        items.append(
            VendorListItem(
                id=v.id,
                canonical_key=v.canonical_key,
                name=v.name,
                region=v.region,
                status=v.status,
                parent_vendor_id=v.parent_vendor_id,
                parent_name=parent_name,
                aliases=alias_map.get(v.id, []),
                active_contracts_count=contract_count_map.get(v.id, 0),
                security_status=sec_status_map.get(v.id, "none"),
                spend_by_currency=spend_dict,
                merged_into_id=v.merged_into_id,
            )
        )

    return items


@router.get("/graph", response_model=VendorGraphResponse, summary="Get enterprise vendor graph")
async def get_vendor_graph(
    auth: AuthContext = Depends(require_permission("vendors:read")),
    db: AsyncSession = Depends(get_async_session),
) -> VendorGraphResponse:
    """Return graph projection of vendors and contracts for the tenant."""
    t = auth.tenant_id

    # Vendors
    q_vendors = await db.execute(select(VendorModel).where(VendorModel.tenant_id == t))
    vendors = q_vendors.scalars().all()

    # Spend per currency
    q_spend = await db.execute(
        select(SpendTransactionModel).where(SpendTransactionModel.tenant_id == t)
    )
    spend_rows = q_spend.scalars().all()
    spend_map: dict[uuid.UUID, dict[str, float]] = {}
    for s in spend_rows:
        spend_map.setdefault(s.vendor_id, {})
        curr = s.currency.upper()
        spend_map[s.vendor_id][curr] = float(
            Decimal(str(spend_map[s.vendor_id].get(curr, 0.0))) + s.amount
        )

    # Contracts
    q_contracts = await db.execute(select(ContractModel).where(ContractModel.tenant_id == t))
    contracts = q_contracts.scalars().all()

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    # Vendor nodes
    for v in vendors:
        nodes.append(
            GraphNode(
                id=str(v.id),
                label="vendor",
                name=v.name,
                status=v.status,
                spend_by_currency=spend_map.get(v.id, {}),
                region=v.region,
            )
        )
        if v.parent_vendor_id:
            edges.append(
                GraphEdge(
                    source=str(v.id),
                    target=str(v.parent_vendor_id),
                    type="SUBSIDIARY_OF",
                )
            )
        if v.status == "merged" and v.merged_into_id:
            edges.append(
                GraphEdge(
                    source=str(v.id),
                    target=str(v.merged_into_id),
                    type="MERGED_INTO",
                )
            )

    # Contract nodes
    for c in contracts:
        nodes.append(
            GraphNode(
                id=str(c.id),
                label="contract",
                title=c.title,
                status=c.status,
                total_value=float(c.total_value),
                currency=c.currency,
                vendor_id=str(c.vendor_id),
            )
        )
        edges.append(
            GraphEdge(
                source=str(c.id),
                target=str(c.vendor_id),
                type="CONTRACT_WITH",
            )
        )

    return VendorGraphResponse(nodes=nodes, edges=edges)


@router.get("/{id}", response_model=VendorDetailResponse, summary="Get vendor detail")
async def get_vendor_detail(
    id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("vendors:read")),
    db: AsyncSession = Depends(get_async_session),
) -> VendorDetailResponse:
    """Fetch complete vendor record with hierarchy, contracts, spend, security, and audit history."""
    t = auth.tenant_id

    q_v = await db.execute(
        select(VendorModel).where(and_(VendorModel.tenant_id == t, VendorModel.id == id))
    )
    vendor = q_v.scalar_one_or_none()
    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Vendor '{id}' not found",
        )

    parent_name = None
    if vendor.parent_vendor_id:
        q_p = await db.execute(
            select(VendorModel.name).where(
                and_(VendorModel.tenant_id == t, VendorModel.id == vendor.parent_vendor_id)
            )
        )
        parent_name = q_p.scalar_one_or_none()

    # Aliases
    q_al = await db.execute(
        select(VendorAliasModel.alias).where(
            and_(VendorAliasModel.tenant_id == t, VendorAliasModel.vendor_id == id)
        )
    )
    aliases = list(q_al.scalars().all())

    # Child subsidiaries
    q_subs = await db.execute(
        select(VendorModel).where(
            and_(VendorModel.tenant_id == t, VendorModel.parent_vendor_id == id)
        )
    )
    subs = q_subs.scalars().all()
    child_subs = [
        {"id": str(s.id), "name": s.name, "status": s.status, "region": s.region} for s in subs
    ]

    # Merged subsidiaries if this vendor absorbed any
    q_merged_in = await db.execute(
        select(VendorModel.id).where(
            and_(VendorModel.tenant_id == t, VendorModel.merged_into_id == id)
        )
    )
    merged_vendor_ids = list(q_merged_in.scalars().all())
    all_spend_vendor_ids = [id] + merged_vendor_ids

    # Contracts
    q_c = await db.execute(
        select(ContractModel).where(
            and_(ContractModel.tenant_id == t, ContractModel.vendor_id == id)
        )
    )
    contracts = [
        ContractItem(
            id=c.id,
            title=c.title,
            status=c.status,
            start_date=str(c.start_date),
            end_date=str(c.end_date) if c.end_date else None,
            total_value=float(c.total_value),
            currency=c.currency,
            auto_renew=c.auto_renew,
            metadata=c.metadata_ or {},
        )
        for c in q_c.scalars().all()
    ]

    # Spend transactions
    q_sp = await db.execute(
        select(SpendTransactionModel)
        .where(
            and_(
                SpendTransactionModel.tenant_id == t,
                SpendTransactionModel.vendor_id.in_(all_spend_vendor_ids),
            )
        )
        .order_by(SpendTransactionModel.transaction_date.desc())
    )
    spend_rows = q_sp.scalars().all()

    spend_by_currency: dict[str, Decimal] = {}
    spend_items: list[SpendTransactionItem] = []
    for sp in spend_rows:
        curr = sp.currency.upper()
        spend_by_currency[curr] = spend_by_currency.get(curr, Decimal("0")) + sp.amount
        spend_items.append(
            SpendTransactionItem(
                id=sp.id,
                vendor_id=sp.vendor_id,
                transaction_date=str(sp.transaction_date),
                amount=float(sp.amount),
                currency=sp.currency,
                category=sp.category,
                source_system=sp.source_system,
                source_id=sp.source_id,
            )
        )

    # If this vendor is currently merged into another, its active spend is empty
    if vendor.status == "merged":
        final_spend_dict = {}
    else:
        final_spend_dict = {k: float(v) for k, v in sorted(spend_by_currency.items())}

    # Security reviews
    q_sec = await db.execute(
        select(VendorSecurityReviewModel)
        .where(
            and_(
                VendorSecurityReviewModel.tenant_id == t,
                VendorSecurityReviewModel.vendor_id == id,
            )
        )
        .order_by(VendorSecurityReviewModel.created_at.desc())
    )
    sec_reviews = [
        SecurityReviewItem(
            id=s.id,
            doc_type=s.doc_type,
            status=s.status,
            effective_date=str(s.effective_date),
            expiration_date=str(s.expiration_date),
        )
        for s in q_sec.scalars().all()
    ]

    # Audit history
    q_audit = await db.execute(
        select(AuditLogModel)
        .where(
            and_(
                AuditLogModel.tenant_id == t,
                AuditLogModel.entity_type.in_(["vendor", "vendors"]),
                AuditLogModel.entity_id == str(id),
            )
        )
        .order_by(AuditLogModel.created_at.desc())
    )
    audit_history = [
        {
            "id": str(a.id),
            "event_type": a.event_type,
            "actor": a.actor,
            "delta": a.delta,
            "created_at": a.created_at.isoformat(),
        }
        for a in q_audit.scalars().all()
    ]

    return VendorDetailResponse(
        id=vendor.id,
        canonical_key=vendor.canonical_key,
        name=vendor.name,
        normalized_base=vendor.normalized_base,
        region=vendor.region,
        legal_name=vendor.legal_name,
        status=vendor.status,
        previous_status=vendor.previous_status,
        parent_vendor_id=vendor.parent_vendor_id,
        parent_name=parent_name,
        merged_into_id=vendor.merged_into_id,
        metadata=vendor.metadata_ or {},
        aliases=aliases,
        child_subsidiaries=child_subs,
        contracts=contracts,
        spend_transactions=spend_items,
        spend_by_currency=final_spend_dict,
        security_reviews=sec_reviews,
        audit_history=audit_history,
    )


@router.post("/merge", summary="Merge two vendors")
async def merge_vendors(
    payload: MergeVendorsRequest,
    auth: AuthContext = Depends(require_permission("vendors:manage")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Execute reversible vendor merge and record immutable audit trail."""
    manager = ReviewManager(tenant_id=auth.tenant_id)
    try:
        merged_vendor = await manager.merge_vendors(
            session=db,
            source_id=payload.source_id,
            target_id=payload.target_id,
            actor=auth.email,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    audit = AuditLogModel(
        tenant_id=auth.tenant_id,
        event_type="vendor_merged",
        actor=auth.email,
        entity_type="vendor",
        entity_id=str(payload.source_id),
        delta={"source_id": str(payload.source_id), "target_id": str(payload.target_id)},
    )
    db.add(audit)
    await db.commit()

    return {
        "status": "merged",
        "source_id": str(merged_vendor.id),
        "target_id": str(payload.target_id),
        "merged_status": merged_vendor.status,
    }


@router.post("/{id}/unmerge", summary="Reversibly unmerge vendor")
async def unmerge_vendor(
    id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("vendors:manage")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Reversibly unmerge vendor, restoring status, tasks, and spend separation."""
    manager = ReviewManager(tenant_id=auth.tenant_id)
    try:
        restored_vendor = await manager.unmerge_vendors(
            session=db,
            source_id=id,
            actor=auth.email,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    audit = AuditLogModel(
        tenant_id=auth.tenant_id,
        event_type="vendor_unmerged",
        actor=auth.email,
        entity_type="vendor",
        entity_id=str(id),
        delta={"action": "unmerge", "restored_status": restored_vendor.status},
    )
    db.add(audit)
    await db.commit()

    return {
        "status": "unmerged",
        "id": str(restored_vendor.id),
        "restored_status": restored_vendor.status,
        "merged_into_id": restored_vendor.merged_into_id,
    }


@router.post("/{id}/research", response_model=VendorResearchResult, summary="Research vendor")
async def research_vendor(
    id: uuid.UUID,
    payload: ResearchVendorRequest,
    auth: AuthContext = Depends(require_permission("vendors:read")),
    db: AsyncSession = Depends(get_async_session),
) -> VendorResearchResult:
    """Run strict grounded research on vendor against supplied documents with verbatim verification."""
    q_v = await db.execute(
        select(VendorModel).where(
            and_(VendorModel.tenant_id == auth.tenant_id, VendorModel.id == id)
        )
    )
    vendor = q_v.scalar_one_or_none()
    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Vendor '{id}' not found",
        )

    docs = list(payload.documents)
    if payload.text and payload.text.strip():
        docs.append(payload.text.strip())

    agent = VendorResearchAgent()
    result = await agent.research(
        documents=docs,
        vendor_id=str(id),
        vendor_name=vendor.name,
    )

    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=result.error or "Vendor research failed",
        )

    return result


class FlagVendorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    risk_type: str = Field(..., description="E.g. sanctions, operational, security, compliance")
    severity: str = Field(default="high", pattern="^(low|medium|high|critical)$")
    reason: str = Field(..., min_length=3, description="Justification for risk flag")


@router.get("/{id}/relationships", summary="Get vendor relationship graph and contracts")
async def get_vendor_relationships(
    id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("vendors:read")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Retrieve vendor corporate hierarchy (parents, subsidiaries, aliases, contracts, spend)."""
    q_v = await db.execute(
        select(VendorModel).where(
            and_(VendorModel.tenant_id == auth.tenant_id, VendorModel.id == id)
        )
    )
    vendor = q_v.scalar_one_or_none()
    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Vendor '{id}' not found"
        )

    # Parent
    parent_data = None
    if vendor.parent_vendor_id:
        q_p = await db.execute(
            select(VendorModel).where(
                and_(
                    VendorModel.tenant_id == auth.tenant_id,
                    VendorModel.id == vendor.parent_vendor_id,
                )
            )
        )
        parent = q_p.scalar_one_or_none()
        if parent:
            parent_data = {"id": str(parent.id), "name": parent.name, "relationship": "PARENT_OF"}

    # Subsidiaries
    q_subs = await db.execute(
        select(VendorModel).where(
            and_(VendorModel.tenant_id == auth.tenant_id, VendorModel.parent_vendor_id == id)
        )
    )
    subs = [
        {"id": str(s.id), "name": s.name, "relationship": "SUBSIDIARY_OF"}
        for s in q_subs.scalars().all()
    ]

    # Aliases
    q_aliases = await db.execute(
        select(VendorAliasModel).where(
            and_(VendorAliasModel.tenant_id == auth.tenant_id, VendorAliasModel.vendor_id == id)
        )
    )
    aliases = [a.alias for a in q_aliases.scalars().all()]

    # Contracts
    q_contracts = await db.execute(
        select(ContractModel).where(
            and_(ContractModel.tenant_id == auth.tenant_id, ContractModel.vendor_id == id)
        )
    )
    contracts = [
        {
            "id": str(c.id),
            "title": c.title,
            "status": c.status,
            "effective_date": c.effective_date.isoformat() if c.effective_date else None,
        }
        for c in q_contracts.scalars().all()
    ]

    return {
        "vendor_id": str(vendor.id),
        "name": vendor.name,
        "parent": parent_data,
        "subsidiaries": subs,
        "aliases": aliases,
        "contracts": contracts,
    }


@router.post("/{id}/flag", summary="Flag vendor risk")
async def flag_vendor(
    id: uuid.UUID,
    payload: FlagVendorRequest,
    auth: AuthContext = Depends(require_permission("vendors:manage")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Flag a vendor for risk review and dispatch outbound vendor.flagged webhook."""
    q_v = await db.execute(
        select(VendorModel).where(
            and_(VendorModel.tenant_id == auth.tenant_id, VendorModel.id == id)
        )
    )
    vendor = q_v.scalar_one_or_none()
    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Vendor '{id}' not found"
        )

    vendor.status = "flagged"
    await db.flush()

    # Dispatch outbound webhook
    from procure_core.developer.webhooks import WebhookDispatcher

    dispatcher = WebhookDispatcher()
    try:
        await dispatcher.dispatch(
            session=db,
            tenant_id=auth.tenant_id,
            event_type="vendor.flagged",
            payload={
                "vendor_id": str(vendor.id),
                "vendor_name": vendor.name,
                "risk_type": payload.risk_type,
                "severity": payload.severity,
                "reason": payload.reason,
                "flagged_by": auth.email or auth.user_id,
            },
        )
        await db.commit()
    except Exception:
        pass

    return {
        "vendor_id": str(vendor.id),
        "status": "flagged",
        "risk_type": payload.risk_type,
        "severity": payload.severity,
    }
