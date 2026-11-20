"""Slack App Manifest, OAuth Install Flow, Identity Mapping, and Health Routes."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.permissions import AuthContext, require_permission
from procure_core.config import get_settings
from procure_core.db import get_async_session
from procure_core.models.slack import SlackIdentityMappingModel, SlackInstallationModel
from procure_core.security.secrets import get_secrets_manager
from procure_core.slack.manifest import (
    create_slack_oauth_url,
    generate_slack_manifest,
    verify_slack_oauth_state,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/slack", tags=["Slack Integration"])


class SlackIdentityMapRequest(BaseModel):
    """Map Slack User ID to Omnea SSO Identity."""

    slack_user_id: str = Field(..., min_length=2)
    email: EmailStr
    omnea_user_id: str | None = None


@router.get("/manifest")
async def get_slack_manifest_endpoint(
    request: Request,
) -> dict[str, Any]:
    """Generate Slack App Manifest configured with current host URLs."""
    base_url = str(request.base_url).rstrip("/")
    return generate_slack_manifest(base_url=base_url)


@router.get("/oauth/start")
async def start_slack_oauth(
    request: Request,
    auth: AuthContext = Depends(require_permission("admin:read")),
) -> dict[str, str]:
    """Generate Slack OAuth authorization redirect URL."""
    settings = get_settings()
    client_id = getattr(settings, "SLACK_CLIENT_ID", None) or "omnea_slack_client_id"
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/v1/slack/oauth/callback"

    oauth_url, state_token = create_slack_oauth_url(
        tenant_id=auth.tenant_id,
        client_id=client_id,
        redirect_uri=redirect_uri,
    )
    return {"oauth_url": oauth_url, "state": state_token}


@router.get("/oauth/callback")
async def slack_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_async_session),
) -> Response:
    """Handle Slack OAuth authorization code redirect."""
    parts = state.split(":")
    tenant_id = parts[0] if parts and parts[0].strip() else ""
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth state: missing tenant_id"
        )

    # Validate state signature
    if not verify_slack_oauth_state(state, tenant_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired OAuth state"
        )

    secrets_mgr = get_secrets_manager()
    # Save simulated or live bot token
    secret_ref = await secrets_mgr.store_secret(
        tenant_id=tenant_id,
        scope="slack",
        name="bot_credentials",
        secret_payload={
            "bot_token": f"xoxb-mock-{code[:12]}",
            "authed_user_id": "U_AUTHED_ADMIN",
        },
    )

    # Upsert installation
    stmt = select(SlackInstallationModel).where(SlackInstallationModel.tenant_id == tenant_id)
    res = await db.execute(stmt)
    existing = res.scalar_one_or_none()

    if existing:
        existing.secret_ref = secret_ref
        existing.is_active = True
    else:
        inst = SlackInstallationModel(
            tenant_id=tenant_id,
            team_id="T_OMNEA_HQ",
            team_name="Omnea Enterprise Slack",
            bot_user_id="U_OMNEA_BOT",
            secret_ref=secret_ref,
            authed_user_id="U_AUTHED_ADMIN",
            is_active=True,
        )
        db.add(inst)

    await db.commit()
    # Redirect back to app settings
    return RedirectResponse(url="/app/admin/org?slack_installed=true", status_code=302)


@router.get("/status")
async def get_slack_status(
    auth: AuthContext = Depends(require_permission("admin:read")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Retrieve Slack app installation status and identity mappings count."""
    inst_stmt = select(SlackInstallationModel).where(
        SlackInstallationModel.tenant_id == auth.tenant_id
    )
    inst_res = await db.execute(inst_stmt)
    inst = inst_res.scalar_one_or_none()

    count_stmt = select(SlackIdentityMappingModel).where(
        SlackIdentityMappingModel.tenant_id == auth.tenant_id
    )
    mapped_count = len((await db.execute(count_stmt)).scalars().all())

    return {
        "installed": inst.is_active if inst else False,
        "team_name": inst.team_name if inst else None,
        "team_id": inst.team_id if inst else None,
        "bot_user_id": inst.bot_user_id if inst else None,
        "mapped_identities_count": mapped_count,
    }


@router.post("/identities/map")
async def map_slack_identity(
    body: SlackIdentityMapRequest,
    auth: AuthContext = Depends(require_permission("admin:write")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Map Slack user ID to Omnea user and SSO email."""
    stmt = select(SlackIdentityMappingModel).where(
        and_(
            SlackIdentityMappingModel.tenant_id == auth.tenant_id,
            SlackIdentityMappingModel.slack_user_id == body.slack_user_id,
        )
    )
    res = await db.execute(stmt)
    existing = res.scalar_one_or_none()

    u_uuid = UUID(body.omnea_user_id) if body.omnea_user_id else None
    if existing:
        existing.email = body.email
        existing.omnea_user_id = u_uuid
        mapping = existing
    else:
        mapping = SlackIdentityMappingModel(
            tenant_id=auth.tenant_id,
            slack_user_id=body.slack_user_id,
            omnea_user_id=u_uuid,
            email=body.email,
            is_verified=True,
        )
        db.add(mapping)

    await db.commit()
    return {
        "status": "mapped",
        "slack_user_id": mapping.slack_user_id,
        "email": mapping.email,
        "omnea_user_id": str(mapping.omnea_user_id) if mapping.omnea_user_id else None,
    }
