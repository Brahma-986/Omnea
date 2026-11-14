"""Slack interactive block action webhook handler with signature validation, approver authorization, and 403 auditing."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from fastapi import APIRouter, Header, HTTPException, Request, Response
from sqlalchemy import text
from temporalio.client import Client

from procure_agents.approval.fallback import (
    authorize_approver,
    format_auth_refused_idempotency_key,
)
from procure_core.config import get_settings
from procure_core.db import get_async_session_factory
from workers.approval.models import HumanDecisionSignal

slack_router = APIRouter(prefix="/api/v1/slack", tags=["slack"])


def verify_slack_signature(
    body: bytes,
    timestamp: str | None,
    signature: str | None,
    signing_secret: str,
) -> bool:
    """Validate Slack HMAC-SHA256 signature."""
    if not timestamp or not signature:
        return False
    # Check timestamp freshness (within 5 minutes)
    try:
        ts = int(timestamp)
    except ValueError:
        return False

    if abs(time.time() - ts) > 60 * 5:
        return False

    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}".encode("utf-8")
    computed_hash = "v0=" + hmac.new(
        signing_secret.encode("utf-8"), sig_basestring, hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(computed_hash, signature)


async def handle_slack_action(
    payload: dict[str, Any],
    temporal_client: Client | None = None,
) -> dict[str, Any]:
    """Process Slack block action, enforce assigned-approver auth check, audit 403 refusals, and dispatch signal."""
    user = payload.get("user", {})
    actor_id = user.get("id") or user.get("username", "unknown_user")

    actions = payload.get("actions", [{}])
    action_data = actions[0] if actions else {}
    action = action_data.get("action_id", "approve")  # approve, reject, request_info
    value_raw = action_data.get("value", "{}")

    try:
        val = json.loads(value_raw) if isinstance(value_raw, str) else value_raw
    except Exception:
        val = {}

    tenant_id = val.get("tenant_id", "default_tenant")
    request_id = val.get("request_id", "")
    requester_id = val.get("requester_id", "")
    assigned_approver_id = val.get("assigned_approver_id", "")
    step_name = val.get("step_name", "finance_review")
    round_num = val.get("round", 1)

    # 0. Check if request or step is already decided (idempotent duplicate click with 0 state change)
    session_factory = get_async_session_factory()
    async with session_factory() as session:
        if request_id:
            import uuid
            try:
                r_uuid = uuid.UUID(request_id)
                row = await session.execute(
                    text("SELECT status FROM requests WHERE tenant_id = :t AND id = :id"),
                    {"t": tenant_id, "id": r_uuid},
                )
                req_status = row.scalar_one_or_none()
                if req_status in ("approved", "rejected", "failed", "cancelled"):
                    return {
                        "status": "already_decided",
                        "code": 200,
                        "message": f"Request {request_id} is already decided (status={req_status})",
                        "replace_original": True,
                        "text": f"Request {request_id} has already been decided: {req_status}",
                    }
                step_row = await session.execute(
                    text("SELECT status FROM approval_steps WHERE tenant_id = :t AND request_id = :id AND step_name = :s AND round = :r"),
                    {"t": tenant_id, "id": r_uuid, "s": step_name, "r": round_num},
                )
                step_status = step_row.scalar_one_or_none()
                if step_status in ("approved", "rejected", "skipped", "cancelled"):
                    return {
                        "status": "already_decided",
                        "code": 200,
                        "message": f"Step {step_name} (round {round_num}) is already decided (status={step_status})",
                        "replace_original": True,
                        "text": f"Step {step_name} has already been decided: {step_status}",
                    }
            except (ValueError, Exception):
                pass

    # 1. Authorize Approver (Real production guard)
    is_authorized = authorize_approver(
        actor_id=actor_id,
        requester_id=requester_id,
        assigned_approver_id=assigned_approver_id,
        is_admin=False,
    )

    if not is_authorized:
        # Audit 403 Refusal with deterministic idempotency key
        idem_key = format_auth_refused_idempotency_key(
            tenant_id, request_id, actor_id, step_name, action, round_num
        )
        session_factory = get_async_session_factory()
        async with session_factory() as session:
            async with session.begin():
                existing = await session.scalar(
                    text(
                        "SELECT id FROM approval_audit_records "
                        "WHERE tenant_id = :tenant_id AND idempotency_key = :idem"
                    ),
                    {"tenant_id": tenant_id, "idem": idem_key},
                )
                if not existing:
                    import uuid
                    req_uuid = uuid.UUID(request_id) if request_id else uuid.uuid4()
                    # Ensure parent request row exists to satisfy FK constraint
                    req_exists = await session.scalar(
                        text("SELECT id FROM requests WHERE tenant_id = :tenant_id AND id = :id"),
                        {"tenant_id": tenant_id, "id": req_uuid},
                    )
                    if not req_exists:
                        await session.execute(
                            text(
                                "INSERT INTO requests (id, tenant_id, requester_id, status) "
                                "VALUES (:id, :tenant_id, :requester_id, 'draft')"
                            ),
                            {"id": req_uuid, "tenant_id": tenant_id, "requester_id": requester_id or "unknown_req"},
                        )

                    await session.execute(
                        text(
                            "INSERT INTO approval_audit_records ("
                            "id, tenant_id, request_id, step_id, actor_id, actor_type, "
                            "action, verdict, reasons, evidence_ids, idempotency_key"
                            ") VALUES ("
                            ":id, :tenant_id, :req_id, NULL, :actor_id, 'human', "
                            "'authorization_refused', NULL, '[\"Unauthorized approver attempted sign-off\"]', '[]', :idem"
                            ")"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "tenant_id": tenant_id,
                            "req_id": req_uuid,
                            "actor_id": actor_id,
                            "idem": idem_key,
                        },
                    )

        return {
            "status": "refused",
            "code": 403,
            "message": f"User '{actor_id}' is not authorized to {action} request '{request_id}'",
        }

    # 2. Authorized: Deliver signal to Temporal workflow
    signal_key = f"{tenant_id}:{request_id}:{step_name}:{actor_id}:{action}:{round_num}"
    container_ts = payload.get("container", {}).get("message_ts") or payload.get("message_ts")
    sig = HumanDecisionSignal(
        actor_id=actor_id,
        step_name=step_name,
        action=action,
        comment=val.get("comment", "Approved via Slack interactive message"),
        slack_ts=container_ts,
        idempotency_key=signal_key,
    )

    # In-place message update (chat.update) instead of reposting a new message
    if container_ts:
        from workers.approval.activities import update_slack_notification_activity
        await update_slack_notification_activity(
            tenant_id=tenant_id,
            request_id_str=request_id,
            slack_ts=container_ts,
            updated_text=f"Decision recorded: *{action.upper()}* by <@{actor_id}>",
        )

    if temporal_client:
        wf_id = f"{tenant_id}:{request_id}"
        handle = temporal_client.get_workflow_handle(wf_id)
        await handle.signal("signal_human_decision", sig.model_dump())

    return {
        "status": "accepted",
        "code": 200,
        "message": f"Action '{action}' successfully recorded for request '{request_id}'",
        "replace_original": True,
        "text": f"Decision recorded: *{action.upper()}* by <@{actor_id}>",
    }


@slack_router.post("/interactive")
async def slack_interactive_endpoint(
    request: Request,
    x_slack_request_timestamp: str | None = Header(None, alias="X-Slack-Request-Timestamp"),
    x_slack_signature: str | None = Header(None, alias="X-Slack-Signature"),
) -> Response:
    """Slack Interactivity Webhook Endpoint."""
    body_bytes = await request.body()
    settings = get_settings()

    # Signature verification
    if not verify_slack_signature(
        body_bytes,
        x_slack_request_timestamp,
        x_slack_signature,
        settings.SLACK_SIGNING_SECRET,
    ):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = json.loads(body_bytes.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON payload") from None
    else:
        from urllib.parse import parse_qs
        body_str = body_bytes.decode("utf-8")
        parsed = parse_qs(body_str)
        payload_str = parsed.get("payload", [None])[0]
        if not payload_str:
            raise HTTPException(status_code=400, detail="Missing payload")
        try:
            payload = json.loads(payload_str)
        except Exception:
            raise HTTPException(status_code=400, detail="Malformed JSON in payload") from None

    result = await handle_slack_action(payload)
    status_code = result.get("code", 200)
    return Response(content=json.dumps(result), status_code=status_code, media_type="application/json")
