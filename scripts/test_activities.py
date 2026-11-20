import asyncio
import sys
import uuid

sys.path.insert(0, ".")
from tests.integration.test_phase4_workflow_engine import _seed_test_data
from workers.approval.activities import (
    commit_budget_activity,
    publish_outbox_event_activity,
    record_approval_step_activity,
    record_audit_log_activity,
)


async def test():
    t = f"t_act_{uuid.uuid4().hex[:6]}"
    r = uuid.uuid4()
    await _seed_test_data(t, r)
    print("1. seed ok")

    await record_approval_step_activity(
        t,
        str(r),
        "finance_review",
        1,
        "agent",
        "approved",
        "cfo",
        None,
        None,
        False,
        False,
        False,
        {"v": "approve"},
        None,
    )
    print("2. step ok")

    await record_audit_log_activity(
        t,
        str(r),
        "finance_review",
        1,
        "agent_finance",
        "agent",
        "review_completed",
        "approve",
        ["reason1"],
        ["ev1"],
        "v1",
        "gpt",
        "v1",
        "hash1",
        None,
        f"{t}:audit:1",
    )
    print("3. audit ok")

    await commit_budget_activity(t, str(r), 1)
    print("4. commit ok")

    await publish_outbox_event_activity(
        t, str(r), "request.approved", {"ok": True}, f"{t}:outbox:1"
    )
    print("5. outbox ok")


if __name__ == "__main__":
    asyncio.run(test())
