"""Developer Platform API Keys endpoints: create, list, revoke, and rate limits."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.permissions import AuthContext, require_permission
from procure_core.db import get_async_session
from procure_core.developer.api_keys import (
    VALID_SCOPES,
    generate_api_key,
)
from procure_core.models.developer import ApiKeyModel

router = APIRouter(prefix="/api/v1/api-keys", tags=["Developer Platform - API Keys"])


class CreateApiKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=100, description="Descriptive name for key")
    scopes: list[str] = Field(
        default=["requests:read", "requests:create", "vendors:read"],
        description="List of granted permission scopes",
    )
    rate_limit_per_minute: int = Field(
        default=60, ge=1, le=1000, description="Rate limit ceiling per minute"
    )
    expires_in_days: int | None = Field(
        default=None, ge=1, le=365, description="Optional expiry in days"
    )


class CreateApiKeyResponse(BaseModel):
    id: str
    name: str
    key_prefix: str
    raw_key: str
    scopes: list[str]
    rate_limit_per_minute: int
    created_at: str
    expires_at: str | None


class ApiKeyListItem(BaseModel):
    id: str
    name: str
    key_prefix: str
    scopes: list[str]
    rate_limit_per_minute: int
    is_active: bool
    created_at: str
    expires_at: str | None
    revoked_at: str | None
    last_used_at: str | None


@router.post("", response_model=CreateApiKeyResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    req: CreateApiKeyRequest,
    auth: AuthContext = Depends(require_permission("admin:write")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Create a new scoped API key for the authenticated organization."""
    # Validate scopes
    for s in req.scopes:
        if s not in VALID_SCOPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid scope '{s}'. Valid scopes are: {list(VALID_SCOPES)}",
            )

    raw_key, key_model = generate_api_key(
        tenant_id=auth.tenant_id,
        name=req.name,
        scopes=req.scopes,
        rate_limit_per_minute=req.rate_limit_per_minute,
        created_by=auth.email or auth.user_id,
        expires_in_days=req.expires_in_days,
    )

    db.add(key_model)
    await db.flush()

    return {
        "id": str(key_model.id),
        "name": key_model.name,
        "key_prefix": key_model.key_prefix,
        "raw_key": raw_key,
        "scopes": key_model.scopes,
        "rate_limit_per_minute": key_model.rate_limit_per_minute,
        "created_at": key_model.created_at.isoformat(),
        "expires_at": key_model.expires_at.isoformat() if key_model.expires_at else None,
    }


@router.get("", response_model=list[ApiKeyListItem])
async def list_api_keys(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(require_permission("admin:read")),
    db: AsyncSession = Depends(get_async_session),
) -> list[dict[str, Any]]:
    """List API keys for current organization (never reveals secret hash or raw key)."""
    stmt = (
        select(ApiKeyModel)
        .where(ApiKeyModel.tenant_id == auth.tenant_id)
        .order_by(ApiKeyModel.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    res = await db.execute(stmt)
    keys = res.scalars().all()

    now = datetime.now(UTC)
    return [
        {
            "id": str(k.id),
            "name": k.name,
            "key_prefix": k.key_prefix,
            "scopes": k.scopes,
            "rate_limit_per_minute": k.rate_limit_per_minute,
            "is_active": k.revoked_at is None and (k.expires_at is None or k.expires_at > now),
            "created_at": k.created_at.isoformat(),
            "expires_at": k.expires_at.isoformat() if k.expires_at else None,
            "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        }
        for k in keys
    ]


@router.post("/{key_id}/revoke")
async def revoke_api_key(
    key_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("admin:write")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Revoke an API key immediately."""
    stmt = select(ApiKeyModel).where(
        and_(ApiKeyModel.id == key_id, ApiKeyModel.tenant_id == auth.tenant_id)
    )
    res = await db.execute(stmt)
    key_model = res.scalar_one_or_none()
    if not key_model:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")

    if key_model.revoked_at is not None:
        return {"status": "already_revoked", "id": str(key_id)}

    key_model.revoked_at = datetime.now(UTC)
    await db.flush()

    return {"status": "revoked", "id": str(key_id), "revoked_at": key_model.revoked_at.isoformat()}


@router.delete("/{key_id}")
async def delete_api_key(
    key_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("admin:write")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Delete or revoke an API key."""
    return await revoke_api_key(key_id=key_id, auth=auth, db=db)
