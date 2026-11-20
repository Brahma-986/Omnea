"""Approvals routing engine, pending queues, decision processing, and delegation management."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.permissions import AuthContext, require_permission
from api.routes.events import broadcast_tenant_event
from procure_agents.approval.fallback import format_auth_refused_idempotency_key
from procure_agents.approval.idempotency import check_idempotency_seen
from procure_core.db import get_async_session
from procure_core.log_config import get_logger
from procure_core.models.audit import AuditLogModel
from procure_core.models.request import RequestModel

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/approvals", tags=["Approvals"])


# =============================================================================
# Request & Response Schemas
# =============================================================================


class ApprovalDecisionRequest(BaseModel):
    action: Literal["approve", "reject", "request_info"]
    reason: str = Field(min_length=3)
    idempotency_key: str = Field(min_length=1)


class DelegationRequest(BaseModel):
    role: str | None = None
    delegate_id: str | None = None
    delegate_valid_from: datetime | None = None
    delegate_valid_until: datetime | None = None


# =============================================================================
# Routes
# =============================================================================


@router.get("/pending")
async def get_pending_approvals(
    current_user: AuthContext = Depends(require_permission("approvals:read")),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Return pending approval steps assigned to current user, their roles, or where user is an active delegate."""
    actor_id = current_user.user_id
    actor_email = current_user.email
    user_identifiers = {actor_id}
    if actor_email:
        user_identifiers.add(actor_email)

    now = datetime.now(UTC)

    # 1. Resolve any active delegation windows where current user is delegate
    delegated_roles: set[str] = set()
    delegated_users: set[str] = set()

    del_rows = (
        (
            await session.execute(
                text(
                    "SELECT role, user_id, delegate_valid_from, delegate_valid_until "
                    "FROM approver_roles "
                    "WHERE tenant_id = :t "
                    "  AND delegate_id = ANY(:actors) "
                    "  AND delegate_valid_from IS NOT NULL "
                    "  AND delegate_valid_until IS NOT NULL"
                ),
                {"t": current_user.tenant_id, "actors": list(user_identifiers)},
            )
        )
        .mappings()
        .all()
    )

    for d in del_rows:
        v_from = d["delegate_valid_from"]
        v_until = d["delegate_valid_until"]
        if v_from and v_until and v_from <= now <= v_until:
            delegated_roles.add(d["role"])
            delegated_users.add(d["user_id"])

    user_roles = set(current_user.roles) | delegated_roles
    all_matched_assignees = user_identifiers | delegated_users

    # 2. Fetch pending steps
    is_approver_or_admin = bool(
        user_roles & {"admin", "super_admin", "system_admin", "tenant_admin", "approver"}
    )

    if is_approver_or_admin:
        steps_stmt = text(
            "SELECT s.id, s.request_id, s.step_name, s.round, s.reviewer_type, s.status, "
            "       s.assignee, s.t1_reminder_at, s.t2_escalated_at, s.t3_expired_at, "
            "       s.created_at, s.agent_verdict, "
            "       r.requester_id, r.payload "
            "FROM approval_steps s "
            "JOIN requests r ON r.id = s.request_id AND r.tenant_id = s.tenant_id "
            "WHERE s.tenant_id = :t "
            "  AND s.status IN ('pending', 'awaiting_human', 'in_progress', 'needs_human') "
            "ORDER BY s.created_at ASC"
        )
        res = await session.execute(steps_stmt, {"t": current_user.tenant_id})
    else:
        steps_stmt = text(
            "SELECT s.id, s.request_id, s.step_name, s.round, s.reviewer_type, s.status, "
            "       s.assignee, s.t1_reminder_at, s.t2_escalated_at, s.t3_expired_at, "
            "       s.created_at, s.agent_verdict, "
            "       r.requester_id, r.payload "
            "FROM approval_steps s "
            "JOIN requests r ON r.id = s.request_id AND r.tenant_id = s.tenant_id "
            "WHERE s.tenant_id = :t "
            "  AND s.status IN ('pending', 'awaiting_human', 'in_progress', 'needs_human') "
            "  AND ( "
            "       s.assignee = ANY(:assignees) "
            "       OR s.step_name = ANY(:roles) "
            "  ) "
            "ORDER BY s.created_at ASC"
        )
        res = await session.execute(
            steps_stmt,
            {
                "t": current_user.tenant_id,
                "assignees": list(all_matched_assignees),
                "roles": list(user_roles),
            },
        )

    rows = res.mappings().all()

    items: list[dict[str, Any]] = []
    for r in rows:
        created_dt = r.get("created_at") or now
        t1 = r.get("t1_reminder_at")
        t2 = r.get("t2_escalated_at")
        t3 = r.get("t3_expired_at")

        # SLA Status Calculation
        if t3 or (created_dt and now >= created_dt + timedelta(hours=72)):
            sla_status = "expired"
        elif t2 or (created_dt and now >= created_dt + timedelta(hours=48)):
            sla_status = "escalated"
        elif t1 or (created_dt and now >= created_dt + timedelta(hours=24)):
            sla_status = "reminder_due"
        else:
            sla_status = "on_track"

        payload = r.get("payload") or {}
        agent_v = r.get("agent_verdict") or {}
        if isinstance(agent_v, str):
            agent_v = json.loads(agent_v)

        items.append(
            {
                "step_id": str(r["id"]),
                "request_id": str(r["request_id"]),
                "step_name": r["step_name"],
                "round": r["round"],
                "status": r["status"],
                "assignee": r["assignee"],
                "requester_id": r["requester_id"],
                "vendor_name": payload.get("vendor_name"),
                "estimated_spend": payload.get("estimated_spend"),
                "currency": payload.get("currency"),
                "department": payload.get("department"),
                "created_at": created_dt.isoformat(),
                "sla_status": sla_status,
                "t1_reminder_at": t1.isoformat() if t1 else None,
                "t2_escalated_at": t2.isoformat() if t2 else None,
                "t3_expired_at": t3.isoformat() if t3 else None,
                "agent_verdict": agent_v,
            }
        )

    return {
        "pending_approvals": items,
        "count": len(items),
    }


@router.post("/{step_id}/decide")
async def decide_approval_step(
    step_id: str,
    payload: ApprovalDecisionRequest,
    current_user: AuthContext = Depends(require_permission("approvals:decide")),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Execute human decision on an approval step with self-approval guards and stale-click idempotency."""
    try:
        step_uuid = uuid.UUID(step_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Approval step not found"
        ) from None

    actor = current_user.email or current_user.user_id

    # 1. Fetch step row under lock
    step_res = await session.execute(
        text(
            "SELECT id, tenant_id, request_id, step_name, round, reviewer_type, status, "
            "       assignee, agent_verdict, human_verdict "
            "FROM approval_steps "
            "WHERE id = :id AND tenant_id = :t "
            "FOR UPDATE"
        ),
        {"id": step_uuid, "t": current_user.tenant_id},
    )
    step_row = step_res.mappings().first()
    if not step_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval step not found")

    # 2. Fetch parent request
    request_obj = await session.get(RequestModel, step_row["request_id"])
    if not request_obj or request_obj.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Parent request not found"
        )

    # 3. Self-Approval Refusal: Requester is NEVER authorized to approve their own request
    if request_obj.requester_id in (current_user.email, current_user.user_id):
        refused_key = format_auth_refused_idempotency_key(
            tenant_id=current_user.tenant_id,
            request_id=str(request_obj.id),
            actor_id=actor,
            step_name=step_row["step_name"],
            action=payload.action,
            round_num=step_row["round"],
        )

        # Append immutable audit records to both audit_log and approval_audit_records
        session.add(
            AuditLogModel(
                tenant_id=current_user.tenant_id,
                event_type="auth.self_approval_refused",
                actor=actor,
                entity_type="request",
                entity_id=str(request_obj.id),
                delta={
                    "step_id": str(step_uuid),
                    "action": payload.action,
                    "reason": payload.reason,
                    "detail": "Self-approval is strictly forbidden",
                },
            )
        )

        await session.execute(
            text(
                "INSERT INTO approval_audit_records ("
                "id, tenant_id, request_id, step_id, actor_id, actor_type, "
                "action, verdict, reasons, evidence_ids, idempotency_key"
                ") VALUES ("
                ":id, :t, :req_id, :step_id, :actor, 'human', "
                "'auth.self_approval_refused', 'reject', :reasons, '[]'::jsonb, :idem"
                ") ON CONFLICT (tenant_id, idempotency_key) DO NOTHING"
            ),
            {
                "id": uuid.uuid4(),
                "t": current_user.tenant_id,
                "req_id": request_obj.id,
                "step_id": step_uuid,
                "actor": actor,
                "reasons": json.dumps(["Self-approval is strictly forbidden"]),
                "idem": refused_key,
            },
        )
        await session.commit()

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Self-approval is strictly forbidden",
        )

    # 4. Stale-Click & Duplicate Idempotency Guard
    if step_row["status"] in ("approved", "rejected"):
        human_v = step_row["human_verdict"] or {}
        if isinstance(human_v, str):
            human_v = json.loads(human_v)
        return {
            "status": "idempotent_success",
            "idempotent": True,
            "step_id": str(step_uuid),
            "step_status": step_row["status"],
            "verdict": step_row["status"],
            "human_verdict": human_v,
            "message": "Decision already processed (stale-click guard)",
        }

    seen = await check_idempotency_seen(
        session, current_user.tenant_id, payload.idempotency_key, "approval_audit_records"
    )
    if seen:
        human_v = step_row["human_verdict"] or {}
        if isinstance(human_v, str):
            human_v = json.loads(human_v)
        return {
            "status": "idempotent_success",
            "idempotent": True,
            "step_id": str(step_uuid),
            "step_status": step_row["status"],
            "verdict": step_row["status"],
            "human_verdict": human_v,
            "message": "Idempotency key already processed",
        }

    # 5. Process Valid Decision
    new_step_status: str
    if payload.action == "approve":
        new_step_status = "approved"
    elif payload.action == "reject":
        new_step_status = "rejected"
    else:
        new_step_status = "awaiting_human"

    human_verdict = {
        "action": payload.action,
        "reason": payload.reason,
        "decided_by": actor,
        "idempotency_key": payload.idempotency_key,
        "decided_at": datetime.now(UTC).isoformat(),
    }

    # Update approval_steps row
    await session.execute(
        text(
            "UPDATE approval_steps SET "
            "status = :status, "
            "human_verdict = :human_v, "
            "assignee = :assignee, "
            "updated_at = clock_timestamp() "
            "WHERE id = :id"
        ),
        {
            "id": step_uuid,
            "status": new_step_status,
            "human_v": json.dumps(human_verdict),
            "assignee": actor,
        },
    )

    # Append immutable audit record to approval_audit_records
    await session.execute(
        text(
            "INSERT INTO approval_audit_records ("
            "id, tenant_id, request_id, step_id, actor_id, actor_type, "
            "action, verdict, reasons, evidence_ids, idempotency_key"
            ") VALUES ("
            ":id, :t, :req_id, :step_id, :actor, 'human', "
            ":action, :verdict, :reasons, '[]'::jsonb, :idem"
            ") ON CONFLICT (tenant_id, idempotency_key) DO NOTHING"
        ),
        {
            "id": uuid.uuid4(),
            "t": current_user.tenant_id,
            "req_id": request_obj.id,
            "step_id": step_uuid,
            "actor": actor,
            "action": f"approval.{payload.action}",
            "verdict": payload.action if payload.action in ("approve", "reject") else "needs_human",
            "reasons": json.dumps([payload.reason]),
            "idem": payload.idempotency_key,
        },
    )

    # Handle budget reservation
    if payload.action == "approve":
        bgt_res = (
            await session.execute(
                text(
                    "UPDATE budget_reservations "
                    "SET status = 'committed', committed_at = clock_timestamp() "
                    "WHERE tenant_id = :t AND request_id = :req_id AND status = 'active' "
                    "RETURNING amount, department, currency, period"
                ),
                {"t": current_user.tenant_id, "req_id": request_obj.id},
            )
        ).fetchone()

        if bgt_res:
            amt, dept, curr, period = bgt_res
            await session.execute(
                text(
                    "UPDATE department_budgets "
                    "SET committed = committed + :amt "
                    "WHERE tenant_id = :t AND department = :dept AND currency = :curr AND period = :period"
                ),
                {
                    "t": current_user.tenant_id,
                    "amt": amt,
                    "dept": dept,
                    "curr": curr,
                    "period": period,
                },
            )

    elif payload.action == "reject":
        await session.execute(
            text(
                "UPDATE budget_reservations "
                "SET status = 'released', released_at = clock_timestamp() "
                "WHERE tenant_id = :t AND request_id = :req_id AND status = 'active'"
            ),
            {"t": current_user.tenant_id, "req_id": request_obj.id},
        )

    # Check sibling step statuses and transition overall request status
    all_steps_rows = (
        await session.execute(
            text("SELECT status FROM approval_steps WHERE tenant_id = :t AND request_id = :req_id"),
            {"t": current_user.tenant_id, "req_id": request_obj.id},
        )
    ).fetchall()

    all_statuses = [r[0] for r in all_steps_rows]
    if any(s == "rejected" for s in all_statuses):
        request_obj.status = "rejected"
    elif all(s == "approved" for s in all_statuses):
        request_obj.status = "approved"
    elif payload.action == "request_info":
        request_obj.status = "in_review"

    await session.commit()

    # Outbound webhook dispatch
    try:
        from procure_core.developer.webhooks import WebhookDispatcher

        dispatcher = WebhookDispatcher()
        if request_obj.status == "approved":
            await dispatcher.dispatch(
                session=session,
                tenant_id=current_user.tenant_id,
                event_type="request.approved",
                payload={
                    "request_id": str(request_obj.id),
                    "status": "approved",
                    "vendor_name": request_obj.vendor_name,
                    "estimated_spend": str(request_obj.estimated_spend),
                    "currency": request_obj.currency,
                    "decided_by": actor,
                },
            )
            await session.commit()
        elif request_obj.status == "rejected":
            await dispatcher.dispatch(
                session=session,
                tenant_id=current_user.tenant_id,
                event_type="request.rejected",
                payload={
                    "request_id": str(request_obj.id),
                    "status": "rejected",
                    "vendor_name": request_obj.vendor_name,
                    "reason": payload.reason,
                    "decided_by": actor,
                },
            )
            await session.commit()
    except Exception:
        pass

    # Broadcast real-time SSE event
    broadcast_tenant_event(
        current_user.tenant_id,
        "approval.decided",
        {
            "request_id": str(request_obj.id),
            "step_id": str(step_uuid),
            "action": payload.action,
            "decided_by": actor,
            "request_status": request_obj.status,
        },
    )

    return {
        "status": "decided",
        "step_id": str(step_uuid),
        "action": payload.action,
        "step_status": new_step_status,
        "request_status": request_obj.status,
    }


@router.get("/delegation")
async def get_delegations(
    current_user: AuthContext = Depends(require_permission("approvals:read")),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Return delegation configuration for current user from approver_roles."""
    actor = current_user.user_id
    actor_email = current_user.email
    user_identifiers = [actor]
    if actor_email:
        user_identifiers.append(actor_email)

    res = await session.execute(
        text(
            "SELECT role, user_id, delegate_id, delegate_valid_from, delegate_valid_until, backup_id "
            "FROM approver_roles "
            "WHERE tenant_id = :t AND user_id = ANY(:actors)"
        ),
        {"t": current_user.tenant_id, "actors": user_identifiers},
    )
    rows = res.mappings().all()

    delegations = [
        {
            "role": r["role"],
            "user_id": r["user_id"],
            "delegate_id": r["delegate_id"],
            "delegate_valid_from": (
                r["delegate_valid_from"].isoformat() if r.get("delegate_valid_from") else None
            ),
            "delegate_valid_until": (
                r["delegate_valid_until"].isoformat() if r.get("delegate_valid_until") else None
            ),
            "backup_id": r["backup_id"],
        }
        for r in rows
    ]

    return {"delegations": delegations}


@router.post("/delegation")
async def set_delegation(
    payload: DelegationRequest,
    current_user: AuthContext = Depends(require_permission("approvals:decide")),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Configure Out-Of-Office (OOF) delegate or clear existing delegation."""
    actor = current_user.user_id
    actor_email = current_user.email
    target_role = payload.role or "approver"

    # Self-delegation guard
    if payload.delegate_id and payload.delegate_id in (actor, actor_email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delegate approvals to yourself",
        )

    # Validate window constraint
    if payload.delegate_id:
        if not payload.delegate_valid_from or not payload.delegate_valid_until:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Both delegate_valid_from and delegate_valid_until are required when setting a delegate",
            )
        if payload.delegate_valid_from >= payload.delegate_valid_until:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="delegate_valid_from must be strictly earlier than delegate_valid_until",
            )
        val_from = payload.delegate_valid_from
        val_until = payload.delegate_valid_until
        del_id = payload.delegate_id
    else:
        # Clear delegation
        del_id = None
        val_from = None
        val_until = None

    # Upsert approver_roles record
    existing_row = (
        await session.execute(
            text("SELECT id FROM approver_roles WHERE tenant_id = :t AND role = :role"),
            {"t": current_user.tenant_id, "role": target_role},
        )
    ).fetchone()

    if existing_row:
        await session.execute(
            text(
                "UPDATE approver_roles SET "
                "user_id = :uid, "
                "delegate_id = :del_id, "
                "delegate_valid_from = :val_from, "
                "delegate_valid_until = :val_until, "
                "updated_at = clock_timestamp() "
                "WHERE tenant_id = :t AND role = :role"
            ),
            {
                "t": current_user.tenant_id,
                "role": target_role,
                "uid": actor,
                "del_id": del_id,
                "val_from": val_from,
                "val_until": val_until,
            },
        )
    else:
        await session.execute(
            text(
                "INSERT INTO approver_roles ("
                "id, tenant_id, role, user_id, delegate_id, delegate_valid_from, delegate_valid_until, backup_id"
                ") VALUES ("
                ":id, :t, :role, :uid, :del_id, :val_from, :val_until, :backup"
                ")"
            ),
            {
                "id": uuid.uuid4(),
                "t": current_user.tenant_id,
                "role": target_role,
                "uid": actor,
                "del_id": del_id,
                "val_from": val_from,
                "val_until": val_until,
                "backup": "admin@omnea.ai",
            },
        )

    await session.commit()

    return {
        "status": "updated",
        "role": target_role,
        "user_id": actor,
        "delegate_id": del_id,
        "delegate_valid_from": val_from.isoformat() if val_from else None,
        "delegate_valid_until": val_until.isoformat() if val_until else None,
    }
