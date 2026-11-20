"""Reconciliation API routes for drift detection, finding inspection, and audited repairs."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.permissions import AuthContext, require_permission
from procure_core.db import get_async_session
from procure_core.erp.factory import get_erp_connector
from procure_core.erp.reconciliation import ErpReconciliationService
from procure_core.models.erp import (
    ErpReconciliationFindingModel,
    ErpReconciliationRunModel,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/reconciliation", tags=["ERP Reconciliation"])


class RepairFindingRequest(BaseModel):
    """Payload to authorize and execute an audited repair of reconciliation drift."""

    repair_action: str = Field(
        ..., pattern="^(create_missing_po|resync_status|realign_commitment|cancel_orphan_po)$"
    )
    reason: str = Field(
        ..., min_length=5, description="Mandatory audit explanation for reconciliation repair"
    )


@router.post("/run")
async def trigger_reconciliation_run(
    auth: AuthContext = Depends(require_permission("finance:manage")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Trigger on-demand reconciliation run comparing Omnea SOX records with ERP ledger."""
    connector = await get_erp_connector(db, auth.tenant_id)
    service = ErpReconciliationService(connector=connector, tenant_id=auth.tenant_id)
    report = await service.run_reconciliation(db)
    await db.commit()

    return {
        "run_id": report.run_id,
        "tenant_id": report.tenant_id,
        "status": report.status,
        "total_checked": report.total_checked,
        "drift_count": report.drift_count,
        "findings": report.findings,
        "alerts": [
            {
                "type": a.alert_type,
                "severity": a.severity,
                "message": a.message,
            }
            for a in report.alerts_emitted
        ],
    }


@router.get("/latest")
async def get_latest_reconciliation_report(
    auth: AuthContext = Depends(require_permission("finance:read")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Retrieve outcome of the most recent reconciliation run."""
    stmt = (
        select(ErpReconciliationRunModel)
        .where(ErpReconciliationRunModel.tenant_id == auth.tenant_id)
        .order_by(desc(ErpReconciliationRunModel.started_at))
    )
    res = await db.execute(stmt)
    run_rec = res.scalar_one_or_none()
    if not run_rec:
        return {
            "status": "clean",
            "message": "No reconciliation runs on file. Systems of record in initial clean posture.",
            "total_checked": 0,
            "drift_count": 0,
            "findings": [],
        }

    # Fetch findings for this run
    f_stmt = (
        select(ErpReconciliationFindingModel)
        .where(
            and_(
                ErpReconciliationFindingModel.tenant_id == auth.tenant_id,
                ErpReconciliationFindingModel.run_id == run_rec.id,
            )
        )
        .order_by(desc(ErpReconciliationFindingModel.created_at))
    )
    f_rows = (await db.execute(f_stmt)).scalars().all()

    return {
        "run_id": str(run_rec.id),
        "status": run_rec.status,
        "total_checked": run_rec.total_checked,
        "drift_count": run_rec.drift_count,
        "started_at": run_rec.started_at.isoformat(),
        "completed_at": run_rec.completed_at.isoformat() if run_rec.completed_at else None,
        "findings": [
            {
                "id": str(f.id),
                "finding_type": f.finding_type,
                "po_number": f.po_number,
                "severity": f.severity,
                "omnea_state": f.omnea_state,
                "erp_state": f.erp_state,
                "is_resolved": f.is_resolved,
                "resolution_action": f.resolution_action,
                "resolved_by": f.resolved_by,
                "resolved_at": f.resolved_at.isoformat() if f.resolved_at else None,
            }
            for f in f_rows
        ],
    }


@router.get("/findings")
async def list_reconciliation_findings(
    include_resolved: bool = False,
    auth: AuthContext = Depends(require_permission("finance:read")),
    db: AsyncSession = Depends(get_async_session),
) -> list[dict[str, Any]]:
    """List reconciliation drift findings with resolution status filter."""
    stmt = select(ErpReconciliationFindingModel).where(
        ErpReconciliationFindingModel.tenant_id == auth.tenant_id
    )
    if not include_resolved:
        stmt = stmt.where(ErpReconciliationFindingModel.is_resolved == False)  # noqa: E712

    stmt = stmt.order_by(desc(ErpReconciliationFindingModel.created_at))
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": str(f.id),
            "finding_type": f.finding_type,
            "po_number": f.po_number,
            "severity": f.severity,
            "omnea_state": f.omnea_state,
            "erp_state": f.erp_state,
            "is_resolved": f.is_resolved,
            "resolution_action": f.resolution_action,
            "resolved_by": f.resolved_by,
            "resolved_at": f.resolved_at.isoformat() if f.resolved_at else None,
            "created_at": f.created_at.isoformat(),
        }
        for f in rows
    ]


@router.post("/findings/{finding_id}/repair")
async def repair_reconciliation_finding(
    finding_id: str,
    body: RepairFindingRequest,
    auth: AuthContext = Depends(require_permission("finance:manage")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Execute authorized and audited repair action against an ERP drift finding."""
    connector = await get_erp_connector(db, auth.tenant_id)
    service = ErpReconciliationService(connector=connector, tenant_id=auth.tenant_id)

    try:
        repair_res = await service.execute_repair(
            session=db,
            finding_id=finding_id,
            repair_action=body.repair_action,
            reason=body.reason,
            actor_id=auth.user_id,
            actor_roles=auth.roles,
        )
        await db.commit()
        return repair_res
    except PermissionError as p_err:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(p_err)) from p_err
    except ValueError as v_err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(v_err)) from v_err
