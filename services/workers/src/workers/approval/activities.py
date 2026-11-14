"""Temporal Activities for Procurement Approval Engine."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from temporalio import activity

from procure_agents.approval.fallback import format_auth_refused_idempotency_key
from procure_agents.approval.idempotency import check_idempotency_seen
from procure_agents.approval.models import (
    ApprovalContextSnapshot,
    ApprovalPolicyRules,
    BudgetSnapshot,
    ProcurementIntentSummary,
    ReviewerVerdict,
)
from procure_agents.approval.reviewers import (
    FinanceReviewer,
    LegalReviewer,
    SecurityReviewer,
)
from procure_core.db import get_async_session_factory


# =============================================================================
# 1. Reviewer Activities
# =============================================================================
@activity.defn(name="review_finance")
async def review_finance_activity(
    intent_dict: dict[str, Any],
    context_dict: dict[str, Any],
    policy_dict: dict[str, Any],
    budget_dict: dict[str, Any] | None = None,
    requester_is_approver: bool = False,
) -> dict[str, Any]:
    """Execute deterministic Finance Reviewer with strict fail-closed handling."""
    try:
        intent = ProcurementIntentSummary.model_validate(intent_dict)
        context = ApprovalContextSnapshot.model_validate(context_dict)
        policy = ApprovalPolicyRules.model_validate(policy_dict)
        budget = BudgetSnapshot.model_validate(budget_dict) if budget_dict else None

        reviewer = FinanceReviewer(policy)
        verdict = reviewer.review(intent, context, budget, requester_is_approver=requester_is_approver)
        return verdict.model_dump()
    except Exception as exc:
        return ReviewerVerdict(
            reviewer_type="finance",
            verdict="needs_human",
            reasons=[f"Fail-closed on reviewer error: {type(exc).__name__}: {str(exc)}"],
            suggested_assignee="cfo_or_delegate",
        ).model_dump()


@activity.defn(name="review_security")
async def review_security_activity(
    intent_dict: dict[str, Any],
    context_dict: dict[str, Any],
    policy_dict: dict[str, Any],
    fault_injection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute Security Reviewer with strict fail-closed handling."""
    if fault_injection and fault_injection.get("simulate_malformed_json"):
        return {"malformed": "not a valid ReviewerVerdict payload", "missing_required_fields": True}

    try:
        if fault_injection and fault_injection.get("simulate_unhandled_activity_exception"):
            raise RuntimeError("Fatal unhandled activity error in Security reviewer")

        intent = ProcurementIntentSummary.model_validate(intent_dict)
        context = ApprovalContextSnapshot.model_validate(context_dict)
        policy = ApprovalPolicyRules.model_validate(policy_dict)

        reviewer = SecurityReviewer(policy)
        verdict = reviewer.review(intent, context, fault_injection=fault_injection)
        return verdict.model_dump()
    except Exception as exc:
        return ReviewerVerdict(
            reviewer_type="security",
            verdict="needs_human",
            reasons=[f"Fail-closed on reviewer error: {type(exc).__name__}: {str(exc)}"],
            suggested_assignee="security_team",
        ).model_dump()


@activity.defn(name="review_legal")
async def review_legal_activity(
    intent_dict: dict[str, Any],
    context_dict: dict[str, Any],
    policy_dict: dict[str, Any],
    fault_injection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute Legal Reviewer with strict fail-closed handling."""
    try:
        intent = ProcurementIntentSummary.model_validate(intent_dict)
        context = ApprovalContextSnapshot.model_validate(context_dict)
        policy = ApprovalPolicyRules.model_validate(policy_dict)

        reviewer = LegalReviewer(policy)
        verdict = reviewer.review(intent, context, fault_injection=fault_injection)
        return verdict.model_dump()
    except Exception as exc:
        return ReviewerVerdict(
            reviewer_type="legal",
            verdict="needs_human",
            reasons=[f"Fail-closed on reviewer error: {type(exc).__name__}: {str(exc)}"],
            suggested_assignee="legal_counsel",
        ).model_dump()


# =============================================================================
# 2. Database & State Transition Activities
# =============================================================================
@activity.defn(name="transition_request_status")
async def transition_request_status_activity(
    tenant_id: str,
    request_id_str: str,
    target_status: str,
) -> None:
    """Transition requests.status in Postgres adhering to strict status trigger."""
    req_uuid = uuid.UUID(request_id_str)
    session_factory = get_async_session_factory()
    async with session_factory() as session:
        async with session.begin():
            # Check current status
            curr_status = await session.scalar(
                text("SELECT status FROM requests WHERE tenant_id = :tenant_id AND id = :id FOR UPDATE"),
                {"tenant_id": tenant_id, "id": req_uuid},
            )
            if curr_status == target_status:
                return

            await session.execute(
                text(
                    "UPDATE requests SET status = :target_status, updated_at = clock_timestamp() "
                    "WHERE tenant_id = :tenant_id AND id = :id"
                ),
                {"tenant_id": tenant_id, "id": req_uuid, "target_status": target_status},
            )


@activity.defn(name="reserve_budget_activity")
async def reserve_budget_activity(
    tenant_id: str,
    request_id_str: str,
    round_num: int,
    department: str,
    currency: str,
    period: str,
    amount_str: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Acquires active budget reservation under row-level lock SELECT ... FOR UPDATE."""
    req_uuid = uuid.UUID(request_id_str)
    amount = Decimal(amount_str)
    session_factory = get_async_session_factory()

    async with session_factory() as session:
        async with session.begin():
            # Check if active reservation already exists for this (tenant, request, round)
            existing = (
                await session.execute(
                    text(
                        "SELECT id, status, amount FROM budget_reservations "
                        "WHERE tenant_id = :tenant_id AND request_id = :req_id AND round = :round AND status = 'active' "
                        "FOR UPDATE"
                    ),
                    {"tenant_id": tenant_id, "req_id": req_uuid, "round": round_num},
                )
            ).fetchone()
            if existing:
                return {"reserved": True, "amount": str(existing[2]), "id": str(existing[0])}

            # Row lock on budget record
            budget_row = (
                await session.execute(
                    text(
                        "SELECT id, amount, committed FROM department_budgets "
                        "WHERE tenant_id = :tenant_id AND department = :dept AND currency = :curr AND period = :period "
                        "FOR UPDATE"
                    ),
                    {"tenant_id": tenant_id, "dept": department, "curr": currency, "period": period},
                )
            ).fetchone()

            if not budget_row:
                return {"reserved": False, "reason": f"No budget row found for {department} ({currency})"}

            bgt_id, bgt_amount, bgt_committed = budget_row
            active_reserved = await session.scalar(
                text(
                    "SELECT COALESCE(SUM(amount), 0) FROM budget_reservations "
                    "WHERE tenant_id = :tenant_id AND department = :dept AND currency = :curr AND period = :period "
                    "AND status = 'active'"
                ),
                {"tenant_id": tenant_id, "dept": department, "curr": currency, "period": period},
            ) or Decimal("0.0")

            available_headroom = bgt_amount - bgt_committed - active_reserved
            if amount > available_headroom:
                return {
                    "reserved": False,
                    "reason": f"Insufficient headroom: requested {amount}, available {available_headroom}",
                }

            res_id = uuid.uuid4()
            await session.execute(
                text(
                    "INSERT INTO budget_reservations "
                    "(id, tenant_id, request_id, round, department, currency, period, amount, status, idempotency_key) "
                    "VALUES (:id, :tenant_id, :req_id, :round, :dept, :curr, :period, :amount, 'active', :idem)"
                ),
                {
                    "id": res_id,
                    "tenant_id": tenant_id,
                    "req_id": req_uuid,
                    "round": round_num,
                    "dept": department,
                    "curr": currency,
                    "period": period,
                    "amount": amount,
                    "idem": idempotency_key,
                },
            )
            return {"reserved": True, "amount": str(amount), "id": str(res_id)}


@activity.defn(name="commit_budget_activity")
async def commit_budget_activity(
    tenant_id: str,
    request_id_str: str,
    round_num: int,
) -> bool:
    """Commits active reservation under row lock. Idempotent on replay."""
    req_uuid = uuid.UUID(request_id_str)
    session_factory = get_async_session_factory()

    async with session_factory() as session:
        async with session.begin():
            res_row = (
                await session.execute(
                    text(
                        "SELECT id, department, currency, period, amount, status "
                        "FROM budget_reservations "
                        "WHERE tenant_id = :tenant_id AND request_id = :req_id AND round = :round "
                        "FOR UPDATE"
                    ),
                    {"tenant_id": tenant_id, "req_id": req_uuid, "round": round_num},
                )
            ).fetchone()

            if not res_row:
                return False

            res_id, dept, curr, period, amount, status = res_row
            if status == "committed":
                # Replay: already committed; do NOT double-increment department_budgets
                return True

            if status != "active":
                return False

            # Update reservation to committed
            await session.execute(
                text(
                    "UPDATE budget_reservations "
                    "SET status = 'committed', committed_at = clock_timestamp() "
                    "WHERE id = :id"
                ),
                {"id": res_id},
            )

            # Increment committed spend
            await session.execute(
                text(
                    "UPDATE department_budgets "
                    "SET committed = committed + :amount "
                    "WHERE tenant_id = :tenant_id AND department = :dept AND currency = :curr AND period = :period"
                ),
                {"amount": amount, "tenant_id": tenant_id, "dept": dept, "curr": curr, "period": period},
            )
            return True


@activity.defn(name="release_budget_activity")
async def release_budget_activity(
    tenant_id: str,
    request_id_str: str,
    round_num: int,
) -> bool:
    """Releases active reservation on reject, T3 timeout, cancel, or new round."""
    req_uuid = uuid.UUID(request_id_str)
    session_factory = get_async_session_factory()

    async with session_factory() as session:
        async with session.begin():
            res = await session.execute(
                text(
                    "UPDATE budget_reservations "
                    "SET status = 'released', released_at = clock_timestamp() "
                    "WHERE tenant_id = :tenant_id AND request_id = :req_id AND round = :round AND status = 'active'"
                ),
                {"tenant_id": tenant_id, "req_id": req_uuid, "round": round_num},
            )
            return bool(res.rowcount and res.rowcount > 0)


# Test-only fault injection gate
ENABLE_TEST_FAULT_INJECTION = os.environ.get("ENABLE_TEST_FAULT_INJECTION", "0") == "1"


@activity.defn(name="record_approval_step_activity")
async def record_approval_step_activity(
    tenant_id: str,
    request_id_str: str,
    step_name: str,
    round_num: int,
    reviewer_type: str,
    status: str,
    assignee: str | None = None,
    escalated_to: str | None = None,
    slack_message_ts: str | None = None,
    t1_reminder: bool = False,
    t2_escalated: bool = False,
    t3_expired: bool = False,
    agent_verdict_dict: dict[str, Any] | None = None,
    human_verdict_dict: dict[str, Any] | None = None,
    fired_at: str | None = None,
) -> None:
    """Record or update approval_steps state."""
    req_uuid = uuid.UUID(request_id_str)
    session_factory = get_async_session_factory()
    fired_dt = datetime.fromisoformat(fired_at) if fired_at else None

    async with session_factory() as session:
        async with session.begin():
            # Check if step exists
            existing = (
                await session.execute(
                    text(
                        "SELECT id, status FROM approval_steps "
                        "WHERE tenant_id = :tenant_id AND request_id = :req_id AND step_name = :step AND round = :round "
                        "FOR UPDATE"
                    ),
                    {"tenant_id": tenant_id, "req_id": req_uuid, "step": step_name, "round": round_num},
                )
            ).fetchone()

            if existing:
                step_id, old_status = existing
                if old_status in ("approved", "rejected", "skipped"):
                    # Step is terminal, cannot update status
                    return

                await session.execute(
                    text(
                        "UPDATE approval_steps SET "
                        "status = :status, "
                        "assignee = COALESCE(:assignee, assignee), "
                        "escalated_to = COALESCE(:escalated_to, escalated_to), "
                        "slack_message_ts = COALESCE(:slack_message_ts, slack_message_ts), "
                        "t1_reminder_at = CASE WHEN :t1_reminder THEN COALESCE(:fired_at, clock_timestamp()) ELSE t1_reminder_at END, "
                        "t2_escalated_at = CASE WHEN :t2_escalated THEN COALESCE(:fired_at, clock_timestamp()) ELSE t2_escalated_at END, "
                        "t3_expired_at = CASE WHEN :t3_expired THEN COALESCE(:fired_at, clock_timestamp()) ELSE t3_expired_at END, "
                        "agent_verdict = COALESCE(:agent_verdict, agent_verdict), "
                        "human_verdict = COALESCE(:human_verdict, human_verdict) "
                        "WHERE id = :id"
                    ),
                    {
                        "id": step_id,
                        "status": status,
                        "assignee": assignee,
                        "escalated_to": escalated_to,
                        "slack_message_ts": slack_message_ts,
                        "t1_reminder": t1_reminder,
                        "t2_escalated": t2_escalated,
                        "t3_expired": t3_expired,
                        "agent_verdict": json.dumps(agent_verdict_dict) if agent_verdict_dict else None,
                        "human_verdict": json.dumps(human_verdict_dict) if human_verdict_dict else None,
                        "fired_at": fired_dt,
                    },
                )
            else:
                step_id = uuid.uuid4()
                await session.execute(
                    text(
                        "INSERT INTO approval_steps ("
                        "id, tenant_id, request_id, step_name, round, reviewer_type, status, "
                        "assignee, escalated_to, slack_message_ts, "
                        "t1_reminder_at, t2_escalated_at, t3_expired_at, "
                        "agent_verdict, human_verdict"
                        ") VALUES ("
                        ":id, :tenant_id, :req_id, :step_name, :round, :reviewer_type, :status, "
                        ":assignee, :escalated_to, :slack_message_ts, "
                        "CASE WHEN :t1_reminder THEN COALESCE(:fired_at, clock_timestamp()) ELSE NULL END, "
                        "CASE WHEN :t2_escalated THEN COALESCE(:fired_at, clock_timestamp()) ELSE NULL END, "
                        "CASE WHEN :t3_expired THEN COALESCE(:fired_at, clock_timestamp()) ELSE NULL END, "
                        ":agent_verdict, :human_verdict"
                        ")"
                    ),
                    {
                        "id": step_id,
                        "tenant_id": tenant_id,
                        "req_id": req_uuid,
                        "step_name": step_name,
                        "round": round_num,
                        "reviewer_type": reviewer_type,
                        "status": status,
                        "assignee": assignee,
                        "escalated_to": escalated_to,
                        "slack_message_ts": slack_message_ts,
                        "t1_reminder": t1_reminder,
                        "t2_escalated": t2_escalated,
                        "t3_expired": t3_expired,
                        "agent_verdict": json.dumps(agent_verdict_dict) if agent_verdict_dict else None,
                        "human_verdict": json.dumps(human_verdict_dict) if human_verdict_dict else None,
                        "fired_at": fired_dt,
                    },
                )


@activity.defn(name="record_audit_log_activity")
async def record_audit_log_activity(
    tenant_id: str,
    request_id_str: str,
    step_name: str,
    round_num: int,
    actor_id: str,
    actor_type: str,
    action: str,
    verdict: str | None,
    reasons: list[str],
    evidence_ids: list[str],
    prompt_version: str | None,
    model: str | None,
    policy_version: str | None,
    context_snapshot_hash: str | None,
    slack_message_ts: str | None,
    idempotency_key: str,
) -> bool:
    """Insert immutable audit record into approval_audit_records with strict idempotency."""
    req_uuid = uuid.UUID(request_id_str)
    session_factory = get_async_session_factory()

    async with session_factory() as session:
        async with session.begin():
            # Check if idempotency key already exists via application guard
            if await check_idempotency_seen(session, tenant_id, idempotency_key, "approval_audit_records"):
                # Idempotent replay: already recorded
                return True

            audit_id = uuid.uuid4()
            await session.execute(
                text(
                    "INSERT INTO approval_audit_records ("
                    "id, tenant_id, request_id, step_id, actor_id, actor_type, "
                    "action, verdict, reasons, evidence_ids, prompt_version, model, "
                    "policy_version, context_snapshot_hash, slack_message_ts, idempotency_key"
                    ") VALUES ("
                    ":id, :tenant_id, :req_id, NULL, :actor_id, :actor_type, "
                    ":action, :verdict, :reasons, :evidence_ids, :prompt_version, :model, "
                    ":policy_version, :context_snapshot_hash, :slack_ts, :idem"
                    ")"
                ),
                {
                    "id": audit_id,
                    "tenant_id": tenant_id,
                    "req_id": req_uuid,
                    "actor_id": actor_id,
                    "actor_type": actor_type,
                    "action": action,
                    "verdict": verdict,
                    "reasons": json.dumps(reasons),
                    "evidence_ids": json.dumps(evidence_ids),
                    "prompt_version": prompt_version,
                    "model": model,
                    "policy_version": policy_version,
                    "context_snapshot_hash": context_snapshot_hash,
                    "slack_ts": slack_message_ts,
                    "idem": idempotency_key,
                },
            )
            return True


@activity.defn(name="publish_outbox_event_activity")
async def publish_outbox_event_activity(
    tenant_id: str,
    request_id_str: str,
    event_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
) -> bool:
    """Inserts domain event into domain_events_outbox. Idempotent per request."""
    session_factory = get_async_session_factory()

    async with session_factory() as session:
        async with session.begin():
            if await check_idempotency_seen(session, tenant_id, idempotency_key, "domain_events_outbox"):
                return True

            event_id = uuid.uuid4()
            await session.execute(
                text(
                    "INSERT INTO domain_events_outbox ("
                    "id, tenant_id, aggregate_type, aggregate_id, event_type, idempotency_key, payload, status"
                    ") VALUES ("
                    ":id, :tenant_id, 'procurement_request', :agg_id, :event_type, :idem, :payload, 'pending'"
                    ")"
                ),
                {
                    "id": event_id,
                    "tenant_id": tenant_id,
                    "agg_id": request_id_str,
                    "event_type": event_type,
                    "idem": idempotency_key,
                    "payload": json.dumps(payload),
                },
            )
            return True


# Global mock registry for Slack messages during testing (visible message tracker)
_MOCK_SLACK_MESSAGES: dict[str, dict[str, Any]] = {}


@activity.defn(name="post_slack_notification_activity")
async def post_slack_notification_activity(
    tenant_id: str,
    request_id_str: str,
    step_name: str,
    text_content: str,
    idempotency_key: str,
) -> str:
    """Post interactive message to Slack. Idempotent via DB pending intent; returns deterministic slack_ts."""
    session_factory = get_async_session_factory()
    req_uuid = uuid.UUID(request_id_str)

    # 1. Slack Idempotency Guard: Look up existing message from database on retry
    async with session_factory() as session:
        existing_ts = await session.scalar(
            text(
                "SELECT slack_message_ts FROM approval_steps "
                "WHERE tenant_id = :t AND request_id = :r AND step_name = :s AND slack_message_ts IS NOT NULL"
            ),
            {"t": tenant_id, "r": req_uuid, "s": step_name},
        )
        if existing_ts:
            # On retry: message was already initiated/posted. Return existing ts (in-place update)
            return existing_ts

    # 2. Write pending-intent row BEFORE posting to external API (ensures durability across process crashes)
    import hashlib
    ts_hash = int(hashlib.sha256(f"{tenant_id}:{request_id_str}:{step_name}:{idempotency_key}".encode("utf-8")).hexdigest()[:8], 16) % 1000000
    pending_ts = f"1710000000.{ts_hash:06d}"

    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "INSERT INTO approval_steps (id, tenant_id, request_id, step_name, round, reviewer_type, status, slack_message_ts) "
                    "VALUES (:id, :t, :r, :s, 1, 'agent', 'awaiting_human', :ts) "
                    "ON CONFLICT (tenant_id, request_id, step_name, round) DO UPDATE "
                    "SET slack_message_ts = COALESCE(approval_steps.slack_message_ts, EXCLUDED.slack_message_ts)"
                ),
                {"id": uuid.uuid4(), "t": tenant_id, "r": req_uuid, "s": step_name, "ts": pending_ts},
            )

    msg_key = f"{tenant_id}:{request_id_str}:{step_name}"
    _MOCK_SLACK_MESSAGES[msg_key] = {
        "ts": pending_ts,
        "text": text_content,
        "updated": False,
    }

    # Test Fault Injection: simulate crash AFTER external Slack post succeeds, before DB persistence
    if ENABLE_TEST_FAULT_INJECTION and idempotency_key.startswith("sim_post_crash_") and activity.info().attempt == 1:
        raise RuntimeError("Injected failure: worker crashed immediately after fake Slack call, before DB persistence")

    # Simulate worker crash mid-flow before Slack post succeeds if requested
    if ENABLE_TEST_FAULT_INJECTION and idempotency_key.startswith("sim_crash_") and activity.info().attempt == 1:
        raise RuntimeError("Simulated worker process crash mid-flow before Slack post")

    return pending_ts


@activity.defn(name="update_slack_notification_activity")
async def update_slack_notification_activity(
    tenant_id: str,
    request_id_str: str,
    slack_ts: str,
    updated_text: str,
) -> bool:
    """Update existing Slack message in place."""
    for key, data in _MOCK_SLACK_MESSAGES.items():
        if data["ts"] == slack_ts:
            data["text"] = updated_text
            data["updated"] = True
            return True
    return False
