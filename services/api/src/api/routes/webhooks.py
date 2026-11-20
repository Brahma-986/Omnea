"""Outbound Webhooks API: endpoints configuration, delivery logs, and manual redelivery."""

from __future__ import annotations

import secrets
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.permissions import AuthContext, require_permission
from procure_core.db import get_async_session
from procure_core.developer.webhooks import (
    SUPPORTED_WEBHOOK_EVENTS,
    WebhookDispatcher,
)
from procure_core.models.developer import (
    WebhookDeliveryModel,
    WebhookSubscriptionModel,
)

router = APIRouter(prefix="/api/v1/webhooks", tags=["Developer Platform - Webhooks"])


class CreateWebhookEndpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        ..., min_length=10, max_length=1024, description="Destination URL (HTTPS recommended)"
    )
    description: str = Field(default="", max_length=255, description="Endpoint purpose/label")
    events: list[str] = Field(default=["*"], description="Subscribed event types or '*' for all")
    secret: str | None = Field(
        default=None, description="Optional custom signing secret (whsec_...)"
    )


class WebhookEndpointResponse(BaseModel):
    id: str
    url: str
    description: str
    events: list[str]
    is_active: bool
    secret: str
    created_at: str
    updated_at: str


class WebhookDeliveryItem(BaseModel):
    id: str
    subscription_id: str
    event_type: str
    event_id: str | None
    payload: dict[str, Any]
    status: str
    response_status: int | None
    response_body: str | None
    attempt: int
    success: bool
    duration_ms: int
    created_at: str
    delivered_at: str | None


@router.post(
    "/endpoints", response_model=WebhookEndpointResponse, status_code=status.HTTP_201_CREATED
)
async def create_webhook_endpoint(
    req: CreateWebhookEndpointRequest,
    auth: AuthContext = Depends(require_permission("admin:write")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Create a new outbound webhook endpoint."""
    # Validate events
    for e in req.events:
        if e != "*" and e not in SUPPORTED_WEBHOOK_EVENTS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported event '{e}'. Supported events: {list(SUPPORTED_WEBHOOK_EVENTS)}",
            )

    sec = req.secret if req.secret and req.secret.strip() else f"whsec_{secrets.token_hex(24)}"

    sub = WebhookSubscriptionModel(
        tenant_id=auth.tenant_id,
        url=req.url.strip(),
        secret=sec,
        description=req.description.strip(),
        events=req.events,
        is_active=True,
    )
    db.add(sub)
    await db.flush()

    return {
        "id": str(sub.id),
        "url": sub.url,
        "description": sub.description,
        "events": sub.events,
        "is_active": sub.is_active,
        "secret": sub.secret,
        "created_at": sub.created_at.isoformat(),
        "updated_at": sub.updated_at.isoformat(),
    }


@router.get("/endpoints", response_model=list[WebhookEndpointResponse])
async def list_webhook_endpoints(
    auth: AuthContext = Depends(require_permission("admin:read")),
    db: AsyncSession = Depends(get_async_session),
) -> list[dict[str, Any]]:
    """List registered webhook destination endpoints for the organization."""
    stmt = (
        select(WebhookSubscriptionModel)
        .where(WebhookSubscriptionModel.tenant_id == auth.tenant_id)
        .order_by(WebhookSubscriptionModel.created_at.desc())
    )
    res = await db.execute(stmt)
    subs = res.scalars().all()

    return [
        {
            "id": str(s.id),
            "url": s.url,
            "description": s.description,
            "events": s.events,
            "is_active": s.is_active,
            "secret": s.secret,
            "created_at": s.created_at.isoformat(),
            "updated_at": s.updated_at.isoformat(),
        }
        for s in subs
    ]


@router.get("/endpoints/{endpoint_id}", response_model=WebhookEndpointResponse)
async def get_webhook_endpoint(
    endpoint_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("admin:read")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Retrieve details for a specific webhook endpoint."""
    stmt = select(WebhookSubscriptionModel).where(
        and_(
            WebhookSubscriptionModel.id == endpoint_id,
            WebhookSubscriptionModel.tenant_id == auth.tenant_id,
        )
    )
    res = await db.execute(stmt)
    sub = res.scalar_one_or_none()
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Webhook endpoint not found"
        )

    return {
        "id": str(sub.id),
        "url": sub.url,
        "description": sub.description,
        "events": sub.events,
        "is_active": sub.is_active,
        "secret": sub.secret,
        "created_at": sub.created_at.isoformat(),
        "updated_at": sub.updated_at.isoformat(),
    }


@router.delete("/endpoints/{endpoint_id}")
async def delete_webhook_endpoint(
    endpoint_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("admin:write")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Delete a webhook endpoint."""
    stmt = select(WebhookSubscriptionModel).where(
        and_(
            WebhookSubscriptionModel.id == endpoint_id,
            WebhookSubscriptionModel.tenant_id == auth.tenant_id,
        )
    )
    res = await db.execute(stmt)
    sub = res.scalar_one_or_none()
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Webhook endpoint not found"
        )

    await db.delete(sub)
    await db.flush()
    return {"status": "deleted", "id": str(endpoint_id)}


@router.get("/deliveries", response_model=list[WebhookDeliveryItem])
async def list_webhook_deliveries(
    endpoint_id: uuid.UUID | None = None,
    event_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(require_permission("admin:read")),
    db: AsyncSession = Depends(get_async_session),
) -> list[dict[str, Any]]:
    """List delivery attempt logs for webhooks."""
    stmt = select(WebhookDeliveryModel).where(WebhookDeliveryModel.tenant_id == auth.tenant_id)
    if endpoint_id:
        stmt = stmt.where(WebhookDeliveryModel.subscription_id == endpoint_id)
    if event_type:
        stmt = stmt.where(WebhookDeliveryModel.event_type == event_type)

    stmt = stmt.order_by(WebhookDeliveryModel.created_at.desc()).limit(limit).offset(offset)
    res = await db.execute(stmt)
    deliveries = res.scalars().all()

    return [
        {
            "id": str(d.id),
            "subscription_id": str(d.subscription_id),
            "event_type": d.event_type,
            "event_id": d.event_id,
            "payload": d.payload,
            "status": d.status,
            "response_status": d.response_status,
            "response_body": d.response_body,
            "attempt": d.attempt,
            "success": d.success,
            "duration_ms": d.duration_ms,
            "created_at": d.created_at.isoformat(),
            "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None,
        }
        for d in deliveries
    ]


@router.post("/deliveries/{delivery_id}/redeliver")
async def redeliver_webhook(
    delivery_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("admin:write")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Manually re-dispatch a prior webhook delivery attempt."""
    dispatcher = WebhookDispatcher()
    try:
        updated = await dispatcher.redeliver(
            session=db,
            delivery_id=delivery_id,
            tenant_id=auth.tenant_id,
        )
        await db.commit()
        return {
            "id": str(updated.id),
            "status": updated.status,
            "success": updated.success,
            "attempt": updated.attempt,
            "response_status": updated.response_status,
            "duration_ms": updated.duration_ms,
        }
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
