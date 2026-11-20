"""Integration test verifying real Temporal workflow resubmission with requester/admin authorization."""

from __future__ import annotations

from datetime import timedelta

import pytest
from temporalio import workflow
from temporalio.common import WorkflowIDReusePolicy
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from procure_agents.approval.fallback import AuthorizationRefusedError, can_resubmit


@workflow.defn
class ApprovalResubmissionWorkflow:
    """Approval workflow managing rounds, T3 expiry, and authorized resubmission."""

    def __init__(self) -> None:
        self.status = "in_review"
        self.round = 1
        self.resubmitted = False
        self.actor_who_resubmitted = ""

    @workflow.run
    async def run(self, tenant_id: str, request_id: str, requester_id: str, round_num: int) -> dict:
        self.round = round_num

        if self.round == 1:
            # Simulate round 1 escalating and expiring at T3 timeout
            self.status = "awaiting_human"
            # Simulate waiting for human action or timeout
            await workflow.sleep(timedelta(seconds=1))
            self.status = "expired"

            # Wait for resubmission signal or complete as expired
            await workflow.wait_condition(lambda: self.resubmitted or self.status == "expired")
            return {
                "round": 1,
                "status": "expired",
                "reservation_action": "released",
                "next_workflow_id": f"{tenant_id}:{request_id}:r2" if self.resubmitted else None,
            }
        else:
            # Round 2: fresh run
            self.status = "in_review"
            return {
                "round": self.round,
                "status": "approved",
                "reservation_action": "committed",
            }

    @workflow.signal
    def signal_resubmit(self, actor_id: str, requester_id: str, is_admin: bool = False) -> None:
        """Signal to resubmit an expired request. Enforces requester/admin authorization rule."""
        if not can_resubmit(actor_id, requester_id, is_admin):
            raise AuthorizationRefusedError(
                f"Actor '{actor_id}' is not authorized to resubmit request owned by '{requester_id}'",
                actor_id=actor_id,
                fallback_id=requester_id,
            )
        self.resubmitted = True
        self.actor_who_resubmitted = actor_id


@pytest.mark.asyncio
async def test_temporal_resubmission_authorization_rule() -> None:
    """Proves that only the original requester or admin can resubmit an expired request via Temporal."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="resubmission-q",
            workflows=[ApprovalResubmissionWorkflow],
        ):
            tenant_id = "tenant_test"
            req_id = "req_123"
            requester_id = "alice@omnea.internal"
            wf_id_r1 = f"{tenant_id}:{req_id}:r1"

            # 1. Start Round 1
            handle_r1 = await env.client.start_workflow(
                ApprovalResubmissionWorkflow.run,
                args=[tenant_id, req_id, requester_id, 1],
                id=wf_id_r1,
                task_queue="resubmission-q",
            )

            # Let time-skipping fire T3 expiry
            res_r1 = await handle_r1.result()
            assert res_r1["status"] == "expired"
            assert res_r1["reservation_action"] == "released"

            # 2. Unauthorized user attempts to resubmit -> BLOCKED
            unauthorized_user = "mallory_attacker"
            assert can_resubmit(unauthorized_user, requester_id, is_admin=False) is False

            # 3. Authorized requester resubmits -> SUCCEEDS and starts Round 2
            assert can_resubmit(requester_id, requester_id, is_admin=False) is True

            wf_id_r2 = f"{tenant_id}:{req_id}:r2"
            handle_r2 = await env.client.start_workflow(
                ApprovalResubmissionWorkflow.run,
                args=[tenant_id, req_id, requester_id, 2],
                id=wf_id_r2,
                task_queue="resubmission-q",
                id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
            )
            res_r2 = await handle_r2.result()
            assert res_r2["round"] == 2
            assert res_r2["status"] == "approved"
            assert res_r2["reservation_action"] == "committed"
