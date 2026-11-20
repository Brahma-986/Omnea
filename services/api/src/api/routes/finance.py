"""Finance API routes: department budgets, reservations, spend dashboard, policy drafting and confirmation."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.permissions import AuthContext, require_permission
from procure_agents.policy.drafter import (
    DeterministicPolicyValidator,
    PolicyDraftingAgent,
    PolicyDraftResult,
    PolicyValidationResult,
)
from procure_core.db import get_async_session
from procure_core.models.audit import AuditLogModel
from procure_core.models.finance import (
    ApprovalPolicyModel,
    BudgetReservationModel,
    DepartmentBudgetModel,
)
from procure_core.models.vendor import SpendTransactionModel

router = APIRouter(prefix="/api/v1/finance", tags=["Finance"])


# =============================================================================
# Schemas
# =============================================================================


class BudgetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    department: str
    period: str
    currency: str
    amount: float
    committed: float
    active_reserved: float
    headroom: float


class CreateBudgetRequest(BaseModel):
    department: str
    period: str
    currency: str
    amount: Decimal
    committed: Decimal = Decimal("0.0000")


class BudgetReservationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    request_id: uuid.UUID
    round: int
    department: str
    currency: str
    period: str
    amount: float
    status: str
    idempotency_key: str
    created_at: str
    committed_at: str | None = None
    released_at: str | None = None


class CurrencyTotal(BaseModel):
    currency: str
    total: float
    count: int


class SpendDashboardResponse(BaseModel):
    spend_by_currency: dict[str, float] = Field(default_factory=dict)
    spend_by_department: dict[str, dict[str, float]] = Field(default_factory=dict)
    spend_by_category: dict[str, dict[str, float]] = Field(default_factory=dict)
    currency_totals: list[CurrencyTotal] = Field(default_factory=list)
    total_transactions: int = 0


class PolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: str
    content_hash: str
    is_active: bool
    rules: dict[str, Any]
    created_at: str


class PolicyDraftRequest(BaseModel):
    prompt: str
    admin_override: bool = False


class PolicyConfirmRequest(BaseModel):
    version: str | None = None
    rules: dict[str, Any]
    admin_override: bool = False


# =============================================================================
# Endpoints: Budgets & Reservations
# =============================================================================


@router.get("/budgets", response_model=list[BudgetResponse], summary="List department budgets")
async def list_department_budgets(
    department: str | None = Query(None),
    period: str | None = Query(None),
    auth: AuthContext = Depends(require_permission("finance:read")),
    db: AsyncSession = Depends(get_async_session),
) -> list[BudgetResponse]:
    """Return department budgets with committed and active reservation headroom."""
    t = auth.tenant_id

    # 1. Fetch department budgets
    stmt = select(DepartmentBudgetModel).where(DepartmentBudgetModel.tenant_id == t)
    if department:
        stmt = stmt.where(DepartmentBudgetModel.department == department)
    if period:
        stmt = stmt.where(DepartmentBudgetModel.period == period)
    stmt = stmt.order_by(
        DepartmentBudgetModel.department.asc(), DepartmentBudgetModel.currency.asc()
    )

    res = await db.execute(stmt)
    budgets = res.scalars().all()

    # 2. Fetch active reservations aggregated by (department, currency, period)
    res_stmt = (
        select(
            BudgetReservationModel.department,
            BudgetReservationModel.currency,
            BudgetReservationModel.period,
            func.sum(BudgetReservationModel.amount).label("total_reserved"),
        )
        .where(
            and_(
                BudgetReservationModel.tenant_id == t,
                BudgetReservationModel.status == "active",
            )
        )
        .group_by(
            BudgetReservationModel.department,
            BudgetReservationModel.currency,
            BudgetReservationModel.period,
        )
    )
    res_rows = await db.execute(res_stmt)
    active_reserved_map: dict[tuple[str, str, str], Decimal] = {
        (row.department, row.currency, row.period): row.total_reserved for row in res_rows
    }

    results: list[BudgetResponse] = []
    for b in budgets:
        key = (b.department, b.currency, b.period)
        reserved = active_reserved_map.get(key, Decimal("0.0000"))
        headroom = b.amount - b.committed - reserved
        results.append(
            BudgetResponse(
                department=b.department,
                period=b.period,
                currency=b.currency,
                amount=float(b.amount),
                committed=float(b.committed),
                active_reserved=float(reserved),
                headroom=float(headroom),
            )
        )

    return results


@router.post(
    "/budgets", response_model=BudgetResponse, summary="Create or update department budget"
)
async def create_or_update_budget(
    payload: CreateBudgetRequest,
    auth: AuthContext = Depends(require_permission("finance:manage")),
    db: AsyncSession = Depends(get_async_session),
) -> BudgetResponse:
    """Create or update budget record for department, currency, and period."""
    t = auth.tenant_id
    curr = payload.currency.upper()

    q = await db.execute(
        select(DepartmentBudgetModel).where(
            and_(
                DepartmentBudgetModel.tenant_id == t,
                DepartmentBudgetModel.department == payload.department,
                DepartmentBudgetModel.currency == curr,
                DepartmentBudgetModel.period == payload.period,
            )
        )
    )
    existing = q.scalar_one_or_none()

    if existing:
        existing.amount = payload.amount
        existing.committed = payload.committed
        db.add(existing)
        event_type = "budget_updated"
    else:
        existing = DepartmentBudgetModel(
            tenant_id=t,
            department=payload.department,
            currency=curr,
            period=payload.period,
            amount=payload.amount,
            committed=payload.committed,
        )
        db.add(existing)
        event_type = "budget_created"

    await db.flush()

    audit = AuditLogModel(
        tenant_id=t,
        event_type=event_type,
        actor=auth.email,
        entity_type="department_budget",
        entity_id=f"{payload.department}:{curr}:{payload.period}",
        delta={
            "amount": float(payload.amount),
            "committed": float(payload.committed),
            "currency": curr,
            "period": payload.period,
        },
    )
    db.add(audit)
    await db.commit()
    await db.refresh(existing)

    # Calculate active reservation headroom
    res_q = await db.execute(
        select(func.sum(BudgetReservationModel.amount)).where(
            and_(
                BudgetReservationModel.tenant_id == t,
                BudgetReservationModel.department == payload.department,
                BudgetReservationModel.currency == curr,
                BudgetReservationModel.period == payload.period,
                BudgetReservationModel.status == "active",
            )
        )
    )
    reserved = res_q.scalar() or Decimal("0.0000")
    headroom = existing.amount - existing.committed - reserved

    return BudgetResponse(
        department=existing.department,
        period=existing.period,
        currency=existing.currency,
        amount=float(existing.amount),
        committed=float(existing.committed),
        active_reserved=float(reserved),
        headroom=float(headroom),
    )


@router.get(
    "/reservations",
    response_model=list[BudgetReservationResponse],
    summary="List budget reservations",
)
async def list_budget_reservations(
    status_filter: str | None = Query(None, alias="status"),
    department: str | None = Query(None),
    auth: AuthContext = Depends(require_permission("finance:read")),
    db: AsyncSession = Depends(get_async_session),
) -> list[BudgetReservationResponse]:
    """List active and committed budget reservations."""
    t = auth.tenant_id

    stmt = select(BudgetReservationModel).where(BudgetReservationModel.tenant_id == t)
    if status_filter:
        stmt = stmt.where(BudgetReservationModel.status == status_filter)
    if department:
        stmt = stmt.where(BudgetReservationModel.department == department)
    stmt = stmt.order_by(BudgetReservationModel.created_at.desc())

    res = await db.execute(stmt)
    rows = res.scalars().all()

    return [
        BudgetReservationResponse(
            id=r.id,
            request_id=r.request_id,
            round=r.round,
            department=r.department,
            currency=r.currency,
            period=r.period,
            amount=float(r.amount),
            status=r.status,
            idempotency_key=r.idempotency_key,
            created_at=r.created_at.isoformat(),
            committed_at=r.committed_at.isoformat() if r.committed_at else None,
            released_at=r.released_at.isoformat() if r.released_at else None,
        )
        for r in rows
    ]


# =============================================================================
# Endpoints: Spend Dashboard
# =============================================================================


@router.get(
    "/spend-dashboard", response_model=SpendDashboardResponse, summary="Spend analytics dashboard"
)
async def get_spend_dashboard(
    auth: AuthContext = Depends(require_permission("finance:read")),
    db: AsyncSession = Depends(get_async_session),
) -> SpendDashboardResponse:
    """Return spend analytics partitioned strictly per currency. Never sum across currencies."""
    t = auth.tenant_id

    q_sp = await db.execute(
        select(SpendTransactionModel).where(SpendTransactionModel.tenant_id == t)
    )
    txs = q_sp.scalars().all()

    spend_by_currency: dict[str, Decimal] = {}
    spend_by_category: dict[str, dict[str, Decimal]] = {}
    currency_counts: dict[str, int] = {}

    for tx in txs:
        curr = tx.currency.upper()
        spend_by_currency[curr] = spend_by_currency.get(curr, Decimal("0")) + tx.amount
        currency_counts[curr] = currency_counts.get(curr, 0) + 1

        cat = tx.category or "general"
        spend_by_category.setdefault(cat, {})
        spend_by_category[cat][curr] = spend_by_category[cat].get(curr, Decimal("0")) + tx.amount

    currency_totals = [
        CurrencyTotal(
            currency=c,
            total=float(spend_by_currency[c]),
            count=currency_counts[c],
        )
        for c in sorted(spend_by_currency.keys())
    ]

    return SpendDashboardResponse(
        spend_by_currency={c: float(amt) for c, amt in sorted(spend_by_currency.items())},
        spend_by_department={
            cat: {c: float(amt) for c, amt in sorted(currs.items())}
            for cat, currs in sorted(spend_by_category.items())
        },
        spend_by_category={
            cat: {c: float(amt) for c, amt in sorted(currs.items())}
            for cat, currs in sorted(spend_by_category.items())
        },
        currency_totals=currency_totals,
        total_transactions=len(txs),
    )


# =============================================================================
# Endpoints: Approval Policies
# =============================================================================


@router.get(
    "/policies", response_model=list[PolicyResponse], summary="List approval policy versions"
)
async def list_policies(
    auth: AuthContext = Depends(require_permission("finance:read")),
    db: AsyncSession = Depends(get_async_session),
) -> list[PolicyResponse]:
    """List immutable approval policies for the tenant."""
    t = auth.tenant_id
    q = await db.execute(
        select(ApprovalPolicyModel)
        .where(ApprovalPolicyModel.tenant_id == t)
        .order_by(ApprovalPolicyModel.created_at.desc())
    )
    policies = q.scalars().all()

    return [
        PolicyResponse(
            id=p.id,
            version=p.version,
            content_hash=p.content_hash,
            is_active=p.is_active,
            rules=p.rules or {},
            created_at=p.created_at.isoformat(),
        )
        for p in policies
    ]


@router.post(
    "/policies/draft", response_model=PolicyDraftResult, summary="Draft candidate approval policy"
)
async def draft_policy(
    payload: PolicyDraftRequest,
    auth: AuthContext = Depends(require_permission("finance:manage")),
    db: AsyncSession = Depends(get_async_session),
) -> PolicyDraftResult:
    """Draft updated policy rules from plain English rule prompt and validate deterministically."""
    t = auth.tenant_id

    # Fetch currently active policy rules
    q = await db.execute(
        select(ApprovalPolicyModel).where(
            and_(ApprovalPolicyModel.tenant_id == t, ApprovalPolicyModel.is_active.is_(True))
        )
    )
    active = q.scalar_one_or_none()
    active_rules = active.rules if active else None

    agent = PolicyDraftingAgent()
    draft_result = await agent.draft_policy(
        description=payload.prompt,
        existing_rules=active_rules,
        admin_override=payload.admin_override,
    )

    return draft_result


@router.post(
    "/policies/confirm",
    response_model=PolicyResponse,
    summary="Confirm and activate policy version",
)
async def confirm_policy(
    payload: PolicyConfirmRequest,
    auth: AuthContext = Depends(require_permission("finance:manage")),
    db: AsyncSession = Depends(get_async_session),
) -> PolicyResponse:
    """Activate new immutable policy version. Enforces strict schema and anti-weakening validation."""
    t = auth.tenant_id

    # Fetch currently active policy to validate against
    q_active = await db.execute(
        select(ApprovalPolicyModel).where(
            and_(ApprovalPolicyModel.tenant_id == t, ApprovalPolicyModel.is_active.is_(True))
        )
    )
    active_policy = q_active.scalar_one_or_none()
    existing_rules = active_policy.rules if active_policy else None

    # Deterministic validation
    validator = DeterministicPolicyValidator()
    val_res: PolicyValidationResult = validator.validate(
        candidate_data=payload.rules,
        existing_rules=existing_rules,
        admin_override=payload.admin_override,
    )

    if not val_res.valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Policy validation failed: {'; '.join(val_res.reasons)}",
        )

    # Canonicalize and hash rules
    canonical_json = json.dumps(val_res.validated_rules or payload.rules, sort_keys=True)
    c_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    version_name = payload.version
    if not version_name:
        version_name = f"v{int(time.time())}"

    # Deactivate existing active policies for tenant
    await db.execute(
        update(ApprovalPolicyModel)
        .where(and_(ApprovalPolicyModel.tenant_id == t, ApprovalPolicyModel.is_active.is_(True)))
        .values(is_active=False)
    )

    # Insert new immutable policy
    new_policy = ApprovalPolicyModel(
        id=uuid.uuid4(),
        tenant_id=t,
        version=version_name,
        content_hash=c_hash,
        rules=json.loads(canonical_json),
        is_active=True,
    )
    db.add(new_policy)

    audit = AuditLogModel(
        tenant_id=t,
        event_type="policy_confirmed",
        actor=auth.email,
        entity_type="approval_policy",
        entity_id=version_name,
        delta={"version": version_name, "content_hash": c_hash},
    )
    db.add(audit)

    await db.commit()
    await db.refresh(new_policy)

    return PolicyResponse(
        id=new_policy.id,
        version=new_policy.version,
        content_hash=new_policy.content_hash,
        is_active=new_policy.is_active,
        rules=new_policy.rules,
        created_at=new_policy.created_at.isoformat(),
    )
