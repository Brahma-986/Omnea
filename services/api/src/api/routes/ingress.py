"""FastAPI ingress webhook routes for Slack, Teams, and Email."""

import json
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from procure_core.config import Settings, get_settings
from procure_core.db import get_async_session
from procure_core.models.workspace import WorkspaceInstallationModel
from procure_ingress.adapters.email import EmailIngressAdapter
from procure_ingress.adapters.slack import SlackIngressAdapter, verify_slack_signature
from procure_ingress.adapters.teams import TeamsIngressAdapter
from procure_ingress.queue import PostgresInboundQueue

router = APIRouter(prefix="/api/v1/ingress", tags=["Ingress"])


async def resolve_tenant_for_workspace(
    db: AsyncSession,
    platform: str,
    external_workspace_id: str,
    default_tenant_id: str,
) -> tuple[str, str]:
    """Resolve (tenant_id, bot_user_id) from workspace_installations table."""
    if not external_workspace_id:
        return default_tenant_id, ""

    stmt = select(WorkspaceInstallationModel).where(
        (WorkspaceInstallationModel.platform == platform)
        & (WorkspaceInstallationModel.external_workspace_id == external_workspace_id)
    )
    result = await db.execute(stmt)
    installation = result.scalar_one_or_none()
    if installation:
        return installation.tenant_id, installation.bot_user_id

    return default_tenant_id, ""


@router.post(
    "/slack/events",
    summary="Slack Events API webhook endpoint",
    responses={
        200: {"description": "Event acknowledged and enqueued or URL challenge verified"},
        401: {"description": "Invalid signature or expired timestamp"},
    },
)
async def handle_slack_events(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_async_session),
) -> Any:
    """Verify Slack signature, handle URL challenge, filter bot echoes, and fast-enqueue."""
    # 1. Cryptographic signature check over raw request bytes
    raw_body = await verify_slack_signature(request, settings.SLACK_SIGNING_SECRET)

    # 2. Parse JSON body
    try:
        payload: dict[str, Any] = json.loads(raw_body)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON"
        ) from None

    # 3. Handle Slack URL Verification Challenge
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    # 4. Resolve tenant and bot user ID
    adapter = SlackIngressAdapter()
    team_id = payload.get("team_id") or payload.get("event", {}).get("team", "")
    tenant_id, bot_user_id = await resolve_tenant_for_workspace(
        db,
        platform="slack",
        external_workspace_id=team_id,
        default_tenant_id=settings.DEFAULT_TENANT_ID,
    )

    # Ignore bot's own messages if matched
    event = payload.get("event", {})
    if bot_user_id and event.get("user") == bot_user_id:
        return {"ok": True, "ignored": True}

    if adapter.should_ignore(payload):
        return {"ok": True, "ignored": True}

    # 5. Extract keys & normalize
    event_id = adapter.extract_dedup_key(payload)
    ordering_key = adapter.extract_ordering_key(payload)
    normalized = adapter.normalize(payload, tenant_id=tenant_id)

    # 6. Enqueue onto Postgres transactional queue (idempotent ON CONFLICT)
    queue = PostgresInboundQueue(db)
    await queue.enqueue(
        tenant_id=tenant_id,
        channel="slack",
        event_id=event_id,
        ordering_key=ordering_key,
        payload=payload,
        normalized_payload=normalized.model_dump(mode="json"),
    )

    # 7. Acknowledge immediately within 3-second deadline
    return {"ok": True}


@router.post(
    "/slack/commands",
    summary="Slack Slash Commands webhook endpoint",
)
async def handle_slack_commands(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_async_session),
) -> Any:
    """Verify signature, extract slash command payload, enqueue, and respond with ephemeral ack."""
    raw_body = await verify_slack_signature(request, settings.SLACK_SIGNING_SECRET)

    # Parse URL-encoded form data
    body_str = raw_body.decode("utf-8")
    parsed = parse_qs(body_str)
    payload = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}

    team_id = str(payload.get("team_id", ""))
    tenant_id, _ = await resolve_tenant_for_workspace(
        db,
        platform="slack",
        external_workspace_id=team_id,
        default_tenant_id=settings.DEFAULT_TENANT_ID,
    )

    adapter = SlackIngressAdapter()
    event_id = adapter.extract_dedup_key(payload)
    ordering_key = adapter.extract_ordering_key(payload)
    normalized = adapter.normalize(payload, tenant_id=tenant_id)

    queue = PostgresInboundQueue(db)
    await queue.enqueue(
        tenant_id=tenant_id,
        channel="slack",
        event_id=event_id,
        ordering_key=ordering_key,
        payload=payload,
        normalized_payload=normalized.model_dump(mode="json"),
    )

    return {
        "response_type": "ephemeral",
        "text": "Your procurement request has been received and is being processed.",
    }


@router.post(
    "/teams",
    summary="Microsoft Teams Bot Framework webhook endpoint",
)
async def handle_teams_activity(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_async_session),
) -> Any:
    """Handle Teams activity if feature flag is active."""
    adapter = TeamsIngressAdapter(settings)
    adapter.check_enabled()

    payload: dict[str, Any] = await request.json()
    if adapter.should_ignore(payload):
        return {"ok": True, "ignored": True}

    tenant_id = settings.DEFAULT_TENANT_ID
    event_id = adapter.extract_dedup_key(payload)
    ordering_key = adapter.extract_ordering_key(payload)
    normalized = adapter.normalize(payload, tenant_id=tenant_id)

    queue = PostgresInboundQueue(db)
    await queue.enqueue(
        tenant_id=tenant_id,
        channel="teams",
        event_id=event_id,
        ordering_key=ordering_key,
        payload=payload,
        normalized_payload=normalized.model_dump(mode="json"),
    )

    return {"ok": True}


@router.post(
    "/email",
    summary="Inbound Email webhook endpoint",
)
async def handle_email_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_async_session),
) -> Any:
    """Handle inbound email if feature flag is active."""
    adapter = EmailIngressAdapter(settings)
    adapter.check_enabled()

    payload: dict[str, Any] = await request.json()
    if adapter.should_ignore(payload):
        return {"ok": True, "ignored": True}

    tenant_id = settings.DEFAULT_TENANT_ID
    event_id = adapter.extract_dedup_key(payload)
    ordering_key = adapter.extract_ordering_key(payload)
    normalized = adapter.normalize(payload, tenant_id=tenant_id)

    queue = PostgresInboundQueue(db)
    await queue.enqueue(
        tenant_id=tenant_id,
        channel="email",
        event_id=event_id,
        ordering_key=ordering_key,
        payload=payload,
        normalized_payload=normalized.model_dump(mode="json"),
    )

    return {"ok": True}
