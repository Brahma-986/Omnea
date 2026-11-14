"""Integration tests verifying Temporal workflow ID reuse, conflict, and restart semantics."""

from __future__ import annotations

import pytest
from temporalio import workflow
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker


@workflow.defn
class RestartableWorkflow:
    """Workflow that either fails or succeeds based on input."""

    @workflow.run
    async def run(self, should_fail: bool) -> str:
        if should_fail:
            raise ApplicationError("Simulated failure for restart test", non_retryable=True)
        return "SUCCESS"


@pytest.mark.asyncio
async def test_restart_of_failed_request_produces_fresh_run() -> None:
    """Proves that a failed workflow can be restarted with the same workflow_id under ALLOW_DUPLICATE_FAILED_ONLY."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="lifecycle-q",
            workflows=[RestartableWorkflow],
        ):
            wf_id = "request-tenant_1-req_123"

            # 1. Run 1: Fails
            with pytest.raises(Exception) as exc_info:
                await env.client.execute_workflow(
                    RestartableWorkflow.run,
                    True,  # should_fail = True
                    id=wf_id,
                    task_queue="lifecycle-q",
                    id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
                )
            assert "Simulated failure for restart test" in str(getattr(exc_info.value, "cause", exc_info.value))

            # 2. Run 2: Restarting with the same workflow_id produces a fresh successful run
            handle = await env.client.start_workflow(
                RestartableWorkflow.run,
                False,  # should_fail = False
                id=wf_id,
                task_queue="lifecycle-q",
                id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
            )
            result = await handle.result()
            assert result == "SUCCESS"

            # 3. Run 3: Attempting to start again after SUCCESS is blocked by ALLOW_DUPLICATE_FAILED_ONLY
            with pytest.raises(WorkflowAlreadyStartedError):
                await env.client.start_workflow(
                    RestartableWorkflow.run,
                    False,
                    id=wf_id,
                    task_queue="lifecycle-q",
                    id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
                )
