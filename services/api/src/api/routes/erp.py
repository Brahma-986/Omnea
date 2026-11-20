"""ERP Connector Configuration, Testing, Ledger Inspection, and Status Endpoints."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.permissions import AuthContext, require_permission
from procure_core.db import get_async_session
from procure_core.erp.base import ErpConnectionTestResult
from procure_core.erp.factory import get_erp_connector
from procure_core.models.erp import (
    ErpConfigurationModel,
    ErpLedgerCommitmentModel,
    ErpLedgerPurchaseOrderModel,
    ErpSagaExecutionModel,
)
from procure_core.security.secrets import get_secrets_manager, redact_sensitive_data

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/erp", tags=["ERP Systems of Record"])


class ErpConfigUpdateRequest(BaseModel):
    """Payload for configuring tenant ERP integration."""

    connector_type: str = Field(..., pattern="^(ledger|netsuite|sap|workday)$")
    credentials: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)


class ErpConfigResponse(BaseModel):
    """Sanitized ERP configuration response (secrets never exposed)."""

    connector_type: str
    status: str
    secret_ref: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    last_sync_at: str | None = None


class ErpStatusSummary(BaseModel):
    """High-level summary of ERP operational posture."""

    connector_type: str
    connection_status: str
    purchase_orders_count: int
    commitments_count: int
    total_committed_amount: str
    circuit_breaker_state: str
    last_sync_at: str | None = None


@router.get("/config", response_model=ErpConfigResponse)
async def get_erp_config(
    auth: AuthContext = Depends(require_permission("admin:read")),
    db: AsyncSession = Depends(get_async_session),
) -> ErpConfigResponse:
    """Retrieve tenant ERP configuration with credential references (secrets masked)."""
    stmt = (
        select(ErpConfigurationModel)
        .where(ErpConfigurationModel.tenant_id == auth.tenant_id)
        .order_by(desc(ErpConfigurationModel.created_at))
    )
    res = await db.execute(stmt)
    config = res.scalar_one_or_none()

    if not config:
        return ErpConfigResponse(
            connector_type="ledger",
            status="connected",
            secret_ref=None,
            settings={},
            last_sync_at=None,
        )

    return ErpConfigResponse(
        connector_type=config.connector_type,
        status=config.status,
        secret_ref=config.secret_ref,
        settings=redact_sensitive_data(config.settings),
        last_sync_at=config.last_sync_at.isoformat() if config.last_sync_at else None,
    )


@router.post("/config", response_model=ErpConfigResponse)
async def update_erp_config(
    body: ErpConfigUpdateRequest,
    auth: AuthContext = Depends(require_permission("admin:write")),
    db: AsyncSession = Depends(get_async_session),
) -> ErpConfigResponse:
    """Configure tenant ERP integration; store credentials in Secrets Vault, never in DB."""
    secrets_mgr = get_secrets_manager()
    secret_ref = None

    if body.credentials:
        # Store encrypted in secrets vault
        secret_ref = await secrets_mgr.store_secret(
            tenant_id=auth.tenant_id,
            scope="erp",
            name=body.connector_type,
            secret_payload=body.credentials,
        )

    stmt = select(ErpConfigurationModel).where(
        and_(
            ErpConfigurationModel.tenant_id == auth.tenant_id,
            ErpConfigurationModel.connector_type == body.connector_type,
        )
    )
    res = await db.execute(stmt)
    existing = res.scalar_one_or_none()

    if existing:
        existing.status = (
            "connected" if (secret_ref or body.connector_type == "ledger") else "not_connected"
        )
        if secret_ref:
            existing.secret_ref = secret_ref
        existing.settings = redact_sensitive_data(body.settings)
        config = existing
    else:
        config = ErpConfigurationModel(
            tenant_id=auth.tenant_id,
            connector_type=body.connector_type,
            status="connected"
            if (secret_ref or body.connector_type == "ledger")
            else "not_connected",
            secret_ref=secret_ref,
            settings=redact_sensitive_data(body.settings),
        )
        db.add(config)

    await db.commit()
    await db.refresh(config)

    return ErpConfigResponse(
        connector_type=config.connector_type,
        status=config.status,
        secret_ref=config.secret_ref,
        settings=config.settings,
        last_sync_at=config.last_sync_at.isoformat() if config.last_sync_at else None,
    )


@router.post("/test-connection")
async def test_erp_connection(
    connector_type: str | None = None,
    auth: AuthContext = Depends(require_permission("admin:read")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Execute live authentication and connectivity test for tenant ERP."""
    connector = await get_erp_connector(db, auth.tenant_id, connector_type=connector_type)
    test_res: ErpConnectionTestResult = await connector.test_connection()
    return {
        "connected": test_res.connected,
        "connector_type": test_res.connector_type,
        "message": test_res.message,
        "latency_ms": test_res.latency_ms,
        "authenticated_identity": test_res.authenticated_identity,
        "circuit_breaker": connector.circuit_breaker.state,
    }


@router.get("/status", response_model=ErpStatusSummary)
async def get_erp_status(
    auth: AuthContext = Depends(require_permission("finance:read")),
    db: AsyncSession = Depends(get_async_session),
) -> ErpStatusSummary:
    """Retrieve ERP synchronization health, record counts, and circuit breaker status."""
    connector = await get_erp_connector(db, auth.tenant_id)
    test_res = await connector.test_connection()

    # Query counts from built-in ledger
    po_count = (
        await db.scalar(
            select(func.count(ErpLedgerPurchaseOrderModel.id)).where(
                ErpLedgerPurchaseOrderModel.tenant_id == auth.tenant_id
            )
        )
        or 0
    )

    comm_stmt = select(
        func.count(ErpLedgerCommitmentModel.id),
        func.sum(ErpLedgerCommitmentModel.amount),
    ).where(ErpLedgerCommitmentModel.tenant_id == auth.tenant_id)
    comm_res = await db.execute(comm_stmt)
    comm_row = comm_res.one()
    comm_count = comm_row[0] or 0
    total_committed = comm_row[1] or Decimal("0.00")

    return ErpStatusSummary(
        connector_type=connector.connector_type,
        connection_status="connected" if test_res.connected else "not_connected",
        purchase_orders_count=po_count,
        commitments_count=comm_count,
        total_committed_amount=f"{total_committed:.2f}",
        circuit_breaker_state=connector.circuit_breaker.state,
    )


@router.get("/purchase-orders")
async def list_erp_purchase_orders(
    limit: int = 50,
    auth: AuthContext = Depends(require_permission("finance:read")),
    db: AsyncSession = Depends(get_async_session),
) -> list[dict[str, Any]]:
    """List purchase orders directly from ERP ledger records."""
    connector = await get_erp_connector(db, auth.tenant_id)
    pos = await connector.list_purchase_orders(limit=limit)
    return [
        {
            "po_number": p.po_number,
            "request_id": p.request_id,
            "vendor_name": p.vendor_name,
            "amount": str(p.amount),
            "currency": p.currency,
            "status": p.status,
            "erp_po_id": p.erp_po_id,
            "created_at": p.created_at.isoformat(),
        }
        for p in pos
    ]


@router.get("/saga-executions")
async def list_erp_saga_executions(
    limit: int = 50,
    auth: AuthContext = Depends(require_permission("finance:read")),
    db: AsyncSession = Depends(get_async_session),
) -> list[dict[str, Any]]:
    """List recent ERP saga executions, step states, and compensation trails."""
    stmt = (
        select(ErpSagaExecutionModel)
        .where(ErpSagaExecutionModel.tenant_id == auth.tenant_id)
        .order_by(desc(ErpSagaExecutionModel.created_at))
        .limit(limit)
    )
    res = await db.execute(stmt)
    rows = res.scalars().all()
    return [
        {
            "id": str(r.id),
            "saga_id": r.saga_id,
            "request_id": str(r.request_id),
            "status": r.status,
            "current_step": r.current_step,
            "compensation_step": r.compensation_step,
            "po_number": r.po_number,
            "error_detail": r.error_detail,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
