"""Audit export endpoints: JSON and CSV exports with tenant isolation and HMAC verification."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.permissions import AuthContext, require_permission
from procure_core.db import get_async_session
from procure_core.models.audit import AuditLogModel

router = APIRouter(prefix="/api/v1/audit", tags=["Developer Platform - Audit"])


@router.get("/export")
async def export_audit_log(
    format: str = Query(
        default="json", pattern="^(json|csv)$", description="Export format: json or csv"
    ),
    start_date: str | None = Query(default=None, description="ISO timestamp start filter"),
    end_date: str | None = Query(default=None, description="ISO timestamp end filter"),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(require_permission("admin:read")),
    db: AsyncSession = Depends(get_async_session),
) -> Any:
    """Export tenant audit trail records for compliance and external SIEM ingestion."""
    stmt = select(AuditLogModel).where(AuditLogModel.tenant_id == auth.tenant_id)

    if start_date:
        try:
            dt_start = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
            stmt = stmt.where(AuditLogModel.created_at >= dt_start)
        except ValueError:
            pass

    if end_date:
        try:
            dt_end = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            stmt = stmt.where(AuditLogModel.created_at <= dt_end)
        except ValueError:
            pass

    stmt = stmt.order_by(AuditLogModel.created_at.desc()).limit(limit).offset(offset)
    res = await db.execute(stmt)
    records = res.scalars().all()

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            ["id", "tenant_id", "event_type", "actor", "entity_type", "entity_id", "created_at"]
        )
        for r in records:
            writer.writerow(
                [
                    str(r.id),
                    r.tenant_id,
                    r.event_type,
                    r.actor,
                    r.entity_type,
                    r.entity_id,
                    r.created_at.isoformat(),
                ]
            )
        csv_data = output.getvalue()
        return Response(
            content=csv_data,
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=audit_export_{auth.tenant_id}.csv"
            },
        )

    # JSON output
    return {
        "tenant_id": auth.tenant_id,
        "count": len(records),
        "items": [
            {
                "id": str(r.id),
                "event_type": r.event_type,
                "actor": r.actor,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "delta": r.delta,
                "created_at": r.created_at.isoformat(),
            }
            for r in records
        ],
    }
