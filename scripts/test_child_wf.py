import asyncio
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker


@activity.defn
async def sample_activity(x: int) -> int:
    return x * 2


@workflow.defn
class ChildWf:
    @workflow.run
    async def run(self, val: int) -> int:
        return await workflow.execute_activity(
            sample_activity, val, start_to_close_timeout=timedelta(seconds=5)
        )


@workflow.defn
class ParentWf:
    @workflow.run
    async def run(self) -> list[int]:
        c1 = workflow.execute_child_workflow(ChildWf.run, 1, id="c1")
        c2 = workflow.execute_child_workflow(ChildWf.run, 2, id="c2")
        return await asyncio.gather(c1, c2)


async def test():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-q",
            workflows=[ParentWf, ChildWf],
            activities=[sample_activity],
        ):
            res = await env.client.execute_workflow(ParentWf.run, id="p1", task_queue="test-q")
            print("RESULT:", res)


if __name__ == "__main__":
    asyncio.run(test())
