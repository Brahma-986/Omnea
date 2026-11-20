import asyncio
import traceback
from pathlib import Path

from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from workers.approval.workflows import ProcurementApprovalWorkflow


async def main():
    history_path = Path("tests/fixtures/procurement_approval_workflow_history.json")
    with open(history_path, encoding="utf-8") as f:
        raw_json = f.read()
    history = WorkflowHistory.from_json("replay_validation_run", raw_json)
    print(f"Events: {len(history.events)}", flush=True)
    replayer = Replayer(workflows=[ProcurementApprovalWorkflow])
    print("Starting replay with 10s timeout...", flush=True)
    try:
        res = await asyncio.wait_for(replayer.replay_workflow(history), timeout=10.0)
        print("Replay SUCCESS! Result:", res, flush=True)
    except TimeoutError:
        print("TIMED OUT!", flush=True)
    except Exception as e:
        print(f"Exception: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
