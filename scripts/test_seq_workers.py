import asyncio

from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker


@workflow.defn
class SimpleWF:
    def __init__(self):
        self.signal_received = False

    @workflow.run
    async def run(self) -> str:
        await workflow.wait_condition(lambda: self.signal_received)
        return "done"

    @workflow.signal
    def my_signal(self) -> None:
        self.signal_received = True


async def main():
    async with await WorkflowEnvironment.start_local() as env:
        # Start Worker 1
        async with Worker(env.client, task_queue="q", workflows=[SimpleWF], identity="worker-1"):
            handle = await env.client.start_workflow(SimpleWF.run, id="test-wf", task_queue="q")
            await asyncio.sleep(0.5)
            print("Worker 1 started workflow and ran initial turn", flush=True)

        print("Worker 1 shut down completely", flush=True)

        # Start Worker 2
        print("Starting Worker 2...", flush=True)
        async with Worker(env.client, task_queue="q", workflows=[SimpleWF], identity="worker-2"):
            print("Worker 2 started", flush=True)
            await handle.signal(SimpleWF.my_signal)
            print("Signal sent", flush=True)
            res = await handle.result()
            print("Result:", res, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
