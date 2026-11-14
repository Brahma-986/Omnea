"""Temporal Workflows for Parallel Procurement Approval Engine."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from temporalio import workflow
from temporalio.common import RetryPolicy, SearchAttributeKey

with workflow.unsafe.imports_passed_through():
    import procure_agents.approval.fallback as fallback
    from procure_agents.approval.fallback import format_auth_refused_idempotency_key
    from procure_agents.approval.models import ReviewerVerdict
    from workers.approval.models import (
        ApprovalWorkflowInput,
        HumanDecisionSignal,
        WorkflowExecutionResult,
    )
    from workers.approval.activities import (
        commit_budget_activity,
        publish_outbox_event_activity,
        post_slack_notification_activity,
        record_approval_step_activity,
        record_audit_log_activity,
        release_budget_activity,
        reserve_budget_activity,
        review_finance_activity,
        review_legal_activity,
        review_security_activity,
        transition_request_status_activity,
        update_slack_notification_activity,
    )


ACTIVITY_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
)


@workflow.defn(name="FinanceReviewChildWorkflow")
class FinanceReviewChildWorkflow:
    """Child workflow executing deterministic Finance review."""

    @workflow.run
    async def run(
        self,
        intent_dict: dict[str, Any],
        context_dict: dict[str, Any],
        policy_dict: dict[str, Any],
        budget_dict: dict[str, Any] | None,
        requester_is_approver: bool,
    ) -> dict[str, Any]:
        return await workflow.execute_activity(
            review_finance_activity,
            args=[intent_dict, context_dict, policy_dict, budget_dict, requester_is_approver],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=ACTIVITY_RETRY_POLICY,
        )


@workflow.defn(name="SecurityReviewChildWorkflow")
class SecurityReviewChildWorkflow:
    """Child workflow executing Security review."""

    @workflow.run
    async def run(
        self,
        intent_dict: dict[str, Any],
        context_dict: dict[str, Any],
        policy_dict: dict[str, Any],
        fault_injection: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return await workflow.execute_activity(
            review_security_activity,
            args=[intent_dict, context_dict, policy_dict, fault_injection],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=ACTIVITY_RETRY_POLICY,
        )


@workflow.defn(name="LegalReviewChildWorkflow")
class LegalReviewChildWorkflow:
    """Child workflow executing Legal review."""

    @workflow.run
    async def run(
        self,
        intent_dict: dict[str, Any],
        context_dict: dict[str, Any],
        policy_dict: dict[str, Any],
        fault_injection: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return await workflow.execute_activity(
            review_legal_activity,
            args=[intent_dict, context_dict, policy_dict, fault_injection],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=ACTIVITY_RETRY_POLICY,
        )


@workflow.defn(name="ProcurementApprovalWorkflow")
class ProcurementApprovalWorkflow:
    """Parent workflow coordinating parallel reviewers, timers, human signals, and reservation lifecycle."""

    def __init__(self) -> None:
        self.status: str = "draft"
        self.entity_resolved: bool = False
        self.cancelled: bool = False
        self.human_decisions: list[HumanDecisionSignal] = []
        self.duplicate_signals: list[HumanDecisionSignal] = []
        self.requester_replies: list[str] = []
        self.slack_message_ts: str | None = None
        self.decided_signal_keys: set[str] = set()

    @workflow.signal
    def signal_entity_task_resolved(self) -> None:
        """Signal indicating entity review task has completed."""
        self.entity_resolved = True

    @workflow.signal
    def signal_human_decision(self, signal_dict: dict[str, Any]) -> None:
        """Signal delivering human approver decision."""
        signal = HumanDecisionSignal.model_validate(signal_dict)
        # Idempotency guard on human signal: record duplicate for audit logging
        if signal.idempotency_key and signal.idempotency_key in self.decided_signal_keys:
            self.duplicate_signals.append(signal)
            return

        if signal.idempotency_key:
            self.decided_signal_keys.add(signal.idempotency_key)
        self.human_decisions.append(signal)

    @workflow.signal
    def signal_requester_info_reply(self, reply_text: str) -> None:
        """Signal delivering requester clarification on Slack thread."""
        self.requester_replies.append(reply_text)

    @workflow.signal
    def signal_cancel(self, actor_id: str, is_admin: bool = False) -> None:
        """Signal requesting cancellation from requester or admin."""
        self.cancelled = True

    @workflow.run
    async def run(self, input_dict: dict[str, Any]) -> dict[str, Any]:
        inp = ApprovalWorkflowInput.model_validate(input_dict)
        tenant_id = inp.tenant_id
        req_id = inp.request_id
        round_num = inp.round

        # Upsert registered search attributes (TenantId, RequestId, ApprovalStatus, Department, EstimatedSpend)
        try:
            from temporalio.common import SearchAttributeKey
            workflow.upsert_search_attributes([
                SearchAttributeKey.for_keyword("TenantId").value_set(tenant_id),
                SearchAttributeKey.for_keyword("RequestId").value_set(str(req_id)),
                SearchAttributeKey.for_keyword("ApprovalStatus").value_set("in_review"),
                SearchAttributeKey.for_keyword("Department").value_set(inp.intent.department),
                SearchAttributeKey.for_float("EstimatedSpend").value_set(float(inp.intent.estimated_spend)),
            ])
        except Exception:
            pass

        # ---------------------------------------------------------------------
        # 1. Entry Gate Check: Unresolved vendor tasks block review
        # ---------------------------------------------------------------------
        if inp.vendor_has_unresolved_tasks:
            self.status = "awaiting_entity_review"
            await workflow.execute_activity(
                transition_request_status_activity,
                args=[tenant_id, req_id, "awaiting_entity_review"],
                start_to_close_timeout=timedelta(seconds=15),
            )
            await workflow.execute_activity(
                record_approval_step_activity,
                args=[tenant_id, req_id, "entity_resolution", round_num, "human", "needs_human", "procurement_ops"],
                start_to_close_timeout=timedelta(seconds=15),
            )
            # Wait for signal that entity review resolved, or timeout/cancel
            await workflow.wait_condition(lambda: self.entity_resolved or self.cancelled)
            if self.cancelled:
                await workflow.execute_activity(
                    transition_request_status_activity,
                    args=[tenant_id, req_id, "cancelled"],
                    start_to_close_timeout=timedelta(seconds=15),
                )
                return WorkflowExecutionResult(
                    status="cancelled",
                    final_verdict="needs_human",
                    reasons=["Workflow cancelled while awaiting entity review"],
                ).model_dump()

        # ---------------------------------------------------------------------
        # 2. Transition to in_review and acquire active budget reservation
        # ---------------------------------------------------------------------
        self.status = "in_review"
        await workflow.execute_activity(
            transition_request_status_activity,
            args=[tenant_id, req_id, "in_review"],
            start_to_close_timeout=timedelta(seconds=15),
        )

        res_key = f"{tenant_id}:{req_id}:reserve:{round_num}"
        res_info = await workflow.execute_activity(
            reserve_budget_activity,
            args=[
                tenant_id,
                req_id,
                round_num,
                inp.intent.department,
                inp.intent.currency,
                inp.budget.period if inp.budget else "FY2026",
                str(inp.intent.estimated_spend),
                res_key,
            ],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=ACTIVITY_RETRY_POLICY,
        )

        # ---------------------------------------------------------------------
        # 3. Parallel Reviewers (Siblings finish rule checks)
        # ---------------------------------------------------------------------
        intent_d = inp.intent.model_dump(mode="json")
        context_d = inp.context.model_dump(mode="json")
        policy_d = inp.policy.model_dump(mode="json")
        budget_d = inp.budget.model_dump(mode="json") if inp.budget else None

        f_handle = workflow.execute_child_workflow(
            FinanceReviewChildWorkflow.run,
            args=[intent_d, context_d, policy_d, budget_d, False],
            id=f"{tenant_id}:{req_id}:r{round_num}:finance",
        )
        s_handle = workflow.execute_child_workflow(
            SecurityReviewChildWorkflow.run,
            args=[intent_d, context_d, policy_d, inp.fault_injection],
            id=f"{tenant_id}:{req_id}:r{round_num}:security",
        )
        l_handle = workflow.execute_child_workflow(
            LegalReviewChildWorkflow.run,
            args=[intent_d, context_d, policy_d, inp.fault_injection],
            id=f"{tenant_id}:{req_id}:r{round_num}:legal",
        )

        # Await all three child workflows concurrently (siblings finish rule checks)
        raw_results = await asyncio.gather(f_handle, s_handle, l_handle, return_exceptions=True)

        verdicts: dict[str, ReviewerVerdict] = {}
        for name, res in zip(["finance", "security", "legal"], raw_results):
            if isinstance(res, Exception):
                verdicts[name] = ReviewerVerdict(
                    reviewer_type=name,
                    verdict="needs_human",
                    reasons=[f"Fail-closed: Reviewer workflow encountered error: {type(res).__name__}: {str(res)}"],
                    suggested_assignee=f"{name}_lead",
                )
            else:
                try:
                    verdicts[name] = ReviewerVerdict.model_validate(res)
                except Exception as e:
                    verdicts[name] = ReviewerVerdict(
                        reviewer_type=name,
                        verdict="needs_human",
                        reasons=[f"Fail-closed on unparseable verdict payload: {str(e)}"],
                        suggested_assignee=f"{name}_lead",
                    )

        # Record each step and audit row
        for name, v in verdicts.items():
            step_status = "approved" if v.verdict == "approve" else ("rejected" if v.verdict == "reject" else "needs_human")
            await workflow.execute_activity(
                record_approval_step_activity,
                args=[
                    tenant_id,
                    req_id,
                    f"{name}_review",
                    round_num,
                    "agent",
                    step_status,
                    v.suggested_assignee,
                    None,
                    None,
                    False,
                    False,
                    False,
                    v.model_dump(mode="json"),
                    None,
                ],
                start_to_close_timeout=timedelta(seconds=15),
            )
            audit_key = f"{tenant_id}:{req_id}:{name}:agent:{round_num}"
            await workflow.execute_activity(
                record_audit_log_activity,
                args=[
                    tenant_id,
                    req_id,
                    f"{name}_review",
                    round_num,
                    f"agent_{name}",
                    "agent",
                    "review_completed",
                    v.verdict,
                    v.reasons,
                    v.evidence_ids,
                    v.prompt_version,
                    v.model,
                    v.policy_version,
                    inp.context.snapshot_hash,
                    None,
                    audit_key,
                ],
                start_to_close_timeout=timedelta(seconds=15),
            )

        # ---------------------------------------------------------------------
        # 4. Check for Hard Rejects
        # ---------------------------------------------------------------------
        hard_rejects = [v for v in verdicts.values() if v.is_hard_reject]
        if hard_rejects:
            # Rejection path: release budget, transition to rejected, emit domain event
            self.status = "rejected"
            await workflow.execute_activity(
                transition_request_status_activity,
                args=[tenant_id, req_id, "rejected"],
                start_to_close_timeout=timedelta(seconds=15),
            )
            await workflow.execute_activity(
                release_budget_activity,
                args=[tenant_id, req_id, round_num],
                start_to_close_timeout=timedelta(seconds=15),
            )
            outbox_key = f"{tenant_id}:{req_id}:outbox:rejected:{round_num}"
            await workflow.execute_activity(
                publish_outbox_event_activity,
                args=[
                    tenant_id,
                    req_id,
                    "request.rejected",
                    {"trigger": hard_rejects[0].hard_reject_trigger, "reasons": hard_rejects[0].reasons},
                    outbox_key,
                ],
                start_to_close_timeout=timedelta(seconds=15),
            )
            return WorkflowExecutionResult(
                status="rejected",
                final_verdict="reject",
                verdicts=verdicts,
                reservation_action="released",
                reasons=[r for v in verdicts.values() for r in v.reasons],
            ).model_dump(mode="json")

        # ---------------------------------------------------------------------
        # 5. Check if Any Reviewer Needs Human Escalation
        # ---------------------------------------------------------------------
        needs_human_steps = [name for name, v in verdicts.items() if v.verdict == "needs_human"]
        if needs_human_steps:
            self.status = "awaiting_human"
            await workflow.execute_activity(
                transition_request_status_activity,
                args=[tenant_id, req_id, "awaiting_human"],
                start_to_close_timeout=timedelta(seconds=15),
            )

            # Post interactive Slack message with crash injection support
            if inp.fault_injection.get("simulate_slack_post_crash"):
                slack_key = f"sim_post_crash_{round_num}"
            elif inp.fault_injection.get("simulate_worker_crash"):
                slack_key = f"sim_crash_{round_num}"
            else:
                slack_key = f"slack_post_{round_num}"
            slack_text = (
                f"Procurement approval required for Request {req_id} ({inp.intent.vendor_name}, "
                f"{inp.intent.estimated_spend} {inp.intent.currency}). Reviewers needing human sign-off: {needs_human_steps}"
            )
            self.slack_message_ts = await workflow.execute_activity(
                post_slack_notification_activity,
                args=[tenant_id, req_id, "human_escalation", slack_text, slack_key],
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=ACTIVITY_RETRY_POLICY,
            )

            # Update steps with slack_message_ts
            for name in needs_human_steps:
                await workflow.execute_activity(
                    record_approval_step_activity,
                    args=[
                        tenant_id,
                        req_id,
                        f"{name}_review",
                        round_num,
                        "agent",
                        "awaiting_human",
                        verdicts[name].suggested_assignee,
                        None,
                        self.slack_message_ts,
                        False,
                        False,
                        False,
                        verdicts[name].model_dump(mode="json"),
                        None,
                    ],
                    start_to_close_timeout=timedelta(seconds=15),
                )

            # Durable Timers: T1 Reminder, T2 Escalation, T3 Expiry
            t1_duration = timedelta(seconds=inp.t1_seconds)
            t2_duration = timedelta(seconds=inp.t2_seconds)
            t3_duration = timedelta(seconds=inp.t3_seconds)

            # Wait loop with durable timers
            t1_fired = False
            t2_fired = False

            while True:
                wait_timeout = (
                    t1_duration
                    if not t1_fired
                    else (t2_duration - t1_duration if not t2_fired else (t3_duration - t2_duration))
                )
                timed_out = False
                try:
                    await workflow.wait_condition(
                        lambda: len(self.human_decisions) > 0 or self.cancelled,
                        timeout=wait_timeout,
                    )
                except asyncio.TimeoutError:
                    timed_out = True

                if self.cancelled:
                    self.status = "cancelled"
                    await workflow.execute_activity(
                        transition_request_status_activity,
                        args=[tenant_id, req_id, "cancelled"],
                        start_to_close_timeout=timedelta(seconds=15),
                    )
                    await workflow.execute_activity(
                        release_budget_activity,
                        args=[tenant_id, req_id, round_num],
                        start_to_close_timeout=timedelta(seconds=15),
                    )
                    return WorkflowExecutionResult(
                        status="cancelled",
                        final_verdict="needs_human",
                        reservation_action="released",
                        reasons=["Workflow cancelled by user signal"],
                    ).model_dump(mode="json")

                # Process duplicate human signals with standardized duplicate_signal_ignored audit record
                while self.duplicate_signals:
                    dup = self.duplicate_signals.pop(0)
                    dup_key = f"{tenant_id}:{req_id}:dup:{dup.idempotency_key}"
                    await workflow.execute_activity(
                        record_audit_log_activity,
                        args=[
                            tenant_id,
                            req_id,
                            dup.step_name,
                            round_num,
                            dup.actor_id,
                            "human",
                            "duplicate_signal_ignored",
                            None,
                            ["Duplicate human signal received with existing idempotency key; ignored"],
                            [],
                            None,
                            None,
                            None,
                            inp.context.snapshot_hash,
                            self.slack_message_ts,
                            dup_key,
                        ],
                        start_to_close_timeout=timedelta(seconds=15),
                    )

                while self.human_decisions:
                    # Process human decision
                    hd = self.human_decisions.pop(0)
                    assigned_approver = verdicts.get(hd.step_name.replace("_review", ""), ReviewerVerdict(reviewer_type="finance", verdict="needs_human")).suggested_assignee or "cfo_or_delegate"

                    # Backup Approver Guard: Backup approver is only authorized AFTER T2 escalation fires
                    is_backup = (hd.actor_id == "backup_approver")
                    if is_backup and not t2_fired:
                        audit_403_key = format_auth_refused_idempotency_key(
                            tenant_id, req_id, hd.actor_id, hd.step_name, hd.action, round_num
                        )
                        await workflow.execute_activity(
                            record_audit_log_activity,
                            args=[
                                tenant_id,
                                req_id,
                                hd.step_name,
                                round_num,
                                hd.actor_id,
                                "human",
                                "authorization_refused",
                                None,
                                ["Backup approver is not authorized prior to T2 escalation window"],
                                [],
                                None,
                                None,
                                None,
                                inp.context.snapshot_hash,
                                self.slack_message_ts,
                                audit_403_key,
                            ],
                            start_to_close_timeout=timedelta(seconds=15),
                        )
                        continue

                    # Verify authorization
                    if not (is_backup and t2_fired) and not fallback.authorize_approver(hd.actor_id, inp.requester_id, assigned_approver):
                        # 403 Refusal -> log audit record and keep waiting
                        audit_403_key = format_auth_refused_idempotency_key(
                            tenant_id, req_id, hd.actor_id, hd.step_name, hd.action, round_num
                        )
                        await workflow.execute_activity(
                            record_audit_log_activity,
                            args=[
                                tenant_id,
                                req_id,
                                hd.step_name,
                                round_num,
                                hd.actor_id,
                                "human",
                                "authorization_refused",
                                None,
                                ["Actor is not authorized to sign off on this step"],
                                [],
                                None,
                                None,
                                None,
                                inp.context.snapshot_hash,
                                self.slack_message_ts,
                                audit_403_key,
                            ],
                            start_to_close_timeout=timedelta(seconds=15),
                        )
                        continue

                    # Authorized Human Decision
                    human_audit_key = f"{tenant_id}:{req_id}:{hd.step_name}:human:{hd.actor_id}:{hd.action}:{round_num}"
                    human_verdict_str = hd.action if hd.action in ("approve", "reject", "needs_human") else None
                    await workflow.execute_activity(
                        record_audit_log_activity,
                        args=[
                            tenant_id,
                            req_id,
                            hd.step_name,
                            round_num,
                            hd.actor_id,
                            "human",
                            f"human_{hd.action}",
                            human_verdict_str,
                            [hd.comment] if hd.comment else ["Human decision applied"],
                            [],
                            None,
                            None,
                            None,
                            inp.context.snapshot_hash,
                            self.slack_message_ts,
                            human_audit_key,
                        ],
                        start_to_close_timeout=timedelta(seconds=15),
                    )

                    if hd.action == "approve":
                        self.status = "approved"
                        await workflow.execute_activity(
                            commit_budget_activity,
                            args=[tenant_id, req_id, round_num],
                            start_to_close_timeout=timedelta(seconds=15),
                        )
                        await workflow.execute_activity(
                            transition_request_status_activity,
                            args=[tenant_id, req_id, "approved"],
                            start_to_close_timeout=timedelta(seconds=15),
                        )
                        outbox_key = f"{tenant_id}:{req_id}:outbox:approved:{round_num}"
                        await workflow.execute_activity(
                            publish_outbox_event_activity,
                            args=[tenant_id, req_id, "request.approved", {"approved_by": hd.actor_id}, outbox_key],
                            start_to_close_timeout=timedelta(seconds=15),
                        )
                        return WorkflowExecutionResult(
                            status="approved",
                            final_verdict="approve",
                            verdicts=verdicts,
                            reservation_action="committed",
                            reasons=["Human approver authorized request"],
                        ).model_dump(mode="json")

                    elif hd.action == "reject":
                        self.status = "rejected"
                        await workflow.execute_activity(
                            release_budget_activity,
                            args=[tenant_id, req_id, round_num],
                            start_to_close_timeout=timedelta(seconds=15),
                        )
                        await workflow.execute_activity(
                            transition_request_status_activity,
                            args=[tenant_id, req_id, "rejected"],
                            start_to_close_timeout=timedelta(seconds=15),
                        )
                        outbox_key = f"{tenant_id}:{req_id}:outbox:rejected:{round_num}"
                        await workflow.execute_activity(
                            publish_outbox_event_activity,
                            args=[tenant_id, req_id, "request.rejected", {"rejected_by": hd.actor_id}, outbox_key],
                            start_to_close_timeout=timedelta(seconds=15),
                        )
                        return WorkflowExecutionResult(
                            status="rejected",
                            final_verdict="reject",
                            verdicts=verdicts,
                            reservation_action="released",
                            reasons=["Human rejected request"],
                        ).model_dump(mode="json")

                    elif hd.action == "request_info":
                        self.status = "in_review"
                        await workflow.execute_activity(
                            transition_request_status_activity,
                            args=[tenant_id, req_id, "in_review"],
                            start_to_close_timeout=timedelta(seconds=15),
                        )
                        req_info_key = f"{tenant_id}:{req_id}:{hd.step_name}:req_info:{round_num}"
                        await workflow.execute_activity(
                            record_audit_log_activity,
                            args=[
                                tenant_id,
                                req_id,
                                hd.step_name,
                                round_num,
                                hd.actor_id,
                                "human",
                                "request_info_sent",
                                None,
                                [hd.comment or "Clarification requested by approver"],
                                [],
                                None,
                                None,
                                None,
                                inp.context.snapshot_hash,
                                self.slack_message_ts,
                                req_info_key,
                            ],
                            start_to_close_timeout=timedelta(seconds=15),
                        )
                        # Wait for requester thread reply or timeout
                        await workflow.wait_condition(
                            lambda: len(self.requester_replies) > 0 or self.cancelled,
                            timeout=timedelta(seconds=inp.t3_seconds),
                        )
                        if self.cancelled:
                            continue
                        if self.requester_replies:
                            reply_text = self.requester_replies.pop(0)
                            reply_key = f"{tenant_id}:{req_id}:{hd.step_name}:req_reply:{round_num}"
                            await workflow.execute_activity(
                                record_audit_log_activity,
                                args=[
                                    tenant_id,
                                    req_id,
                                    hd.step_name,
                                    round_num,
                                    inp.requester_id,
                                    "human",
                                    "requester_info_replied",
                                    None,
                                    [f"Requester responded: {reply_text}"],
                                    [],
                                    None,
                                    None,
                                    None,
                                    inp.context.snapshot_hash,
                                    self.slack_message_ts,
                                    reply_key,
                                ],
                                start_to_close_timeout=timedelta(seconds=15),
                            )
                            # Clarification provided -> return to awaiting_human for approver sign-off
                            self.status = "awaiting_human"
                            await workflow.execute_activity(
                                transition_request_status_activity,
                                args=[tenant_id, req_id, "awaiting_human"],
                                start_to_close_timeout=timedelta(seconds=15),
                            )
                            continue

                # Handle Timer Expiry
                if timed_out:
                    fired_at_iso = workflow.now().isoformat()
                    if not t1_fired:
                        t1_fired = True
                        for name in needs_human_steps:
                            await workflow.execute_activity(
                                record_approval_step_activity,
                                args=[tenant_id, req_id, f"{name}_review", round_num, "agent", "awaiting_human", None, None, self.slack_message_ts, True, False, False, None, None, fired_at_iso],
                                start_to_close_timeout=timedelta(seconds=15),
                            )
                    elif not t2_fired:
                        t2_fired = True
                        for name in needs_human_steps:
                            await workflow.execute_activity(
                                record_approval_step_activity,
                                args=[tenant_id, req_id, f"{name}_review", round_num, "agent", "awaiting_human", None, "backup_approver", self.slack_message_ts, False, True, False, None, None, fired_at_iso],
                                start_to_close_timeout=timedelta(seconds=15),
                            )
                    else:
                        # T3 Expired!
                        self.status = "expired"
                        for name in needs_human_steps:
                            await workflow.execute_activity(
                                record_approval_step_activity,
                                args=[
                                    tenant_id,
                                    req_id,
                                    f"{name}_review",
                                    round_num,
                                    "agent",
                                    "awaiting_human",
                                    None,
                                    None,
                                    self.slack_message_ts,
                                    False,
                                    False,
                                    True,
                                    None,
                                    None,
                                    fired_at_iso,
                                ],
                                start_to_close_timeout=timedelta(seconds=15),
                            )
                        await workflow.execute_activity(
                            transition_request_status_activity,
                            args=[tenant_id, req_id, "expired"],
                            start_to_close_timeout=timedelta(seconds=15),
                        )
                        await workflow.execute_activity(
                            release_budget_activity,
                            args=[tenant_id, req_id, round_num],
                            start_to_close_timeout=timedelta(seconds=15),
                        )
                        if self.slack_message_ts:
                            await workflow.execute_activity(
                                update_slack_notification_activity,
                                args=[tenant_id, req_id, self.slack_message_ts, "Request expired due to T3 approval timeout."],
                                start_to_close_timeout=timedelta(seconds=15),
                            )
                        return WorkflowExecutionResult(
                            status="expired",
                            final_verdict="needs_human",
                            verdicts=verdicts,
                            reservation_action="released",
                            reasons=["Approval step expired at T3 timeout"],
                        ).model_dump(mode="json")

        # ---------------------------------------------------------------------
        # 6. All Reviewers Approved Path
        # ---------------------------------------------------------------------
        self.status = "approved"
        await workflow.execute_activity(
            commit_budget_activity,
            args=[tenant_id, req_id, round_num],
            start_to_close_timeout=timedelta(seconds=15),
        )
        await workflow.execute_activity(
            transition_request_status_activity,
            args=[tenant_id, req_id, "approved"],
            start_to_close_timeout=timedelta(seconds=15),
        )
        outbox_key = f"{tenant_id}:{req_id}:outbox:approved:{round_num}"
        await workflow.execute_activity(
            publish_outbox_event_activity,
            args=[tenant_id, req_id, "request.approved", {"auto_approved": True}, outbox_key],
            start_to_close_timeout=timedelta(seconds=15),
        )

        return WorkflowExecutionResult(
            status="approved",
            final_verdict="approve",
            verdicts=verdicts,
            reservation_action="committed",
            reasons=["All review checks passed autonomously"],
        ).model_dump(mode="json")
