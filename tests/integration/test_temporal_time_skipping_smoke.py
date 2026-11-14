"""Smoke verification that Temporal time skipping works on this Windows environment."""

from __future__ import annotations

from datetime import timedelta

import pytest
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker


@workflow.defn
class SmokeWorkflow:
    """Minimal workflow executing durable timer sleep."""

    @workflow.run
    async def run(self, name: str) -> str:
        await workflow.sleep(timedelta(seconds=10))
        return f"Hello, {name}!"


@pytest.mark.asyncio
async def test_temporal_time_skipping_smoke_on_windows() -> None:
    """Proves WorkflowEnvironment.start_time_skipping() fast-forwards timers cleanly on Windows."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue="smoke-q", workflows=[SmokeWorkflow]):
            res = await env.client.execute_workflow(
                SmokeWorkflow.run,
                "WindowsSmoke",
                id="smoke-wf-pytest-1",
                task_queue="smoke-q",
            )
            assert res == "Hello, WindowsSmoke!"
