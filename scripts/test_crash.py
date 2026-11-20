import asyncio
import sys
import uuid
from decimal import Decimal

sys.path.insert(0, ".")
from sqlalchemy import text
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from procure_agents.approval.models import (
    ApprovalContextSnapshot,
    ApprovalPolicyRules,
    BudgetSnapshot,
    CitationFact,
    ProcurementIntentSummary,
    SecurityReviewSummary,
)
from procure_core.db import get_async_session_factory
from tests.integration.test_phase4_workflow_engine import (
    APPROVAL_ACTIVITIES,
    APPROVAL_WORKFLOWS,
    _seed_test_data,
)
from workers.approval.models import ApprovalWorkflowInput, HumanDecisionSignal
from workers.approval.workflows import ProcurementApprovalWorkflow


async def test_crash():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        tenant_id = f"t_cr_{uuid.uuid4().hex[:6]}"
        req_id = uuid.uuid4()
        requester_id = "usr_crash"
        await _seed_test_data(tenant_id, req_id, requester_id=requester_id)

        inp = ApprovalWorkflowInput(
            tenant_id=tenant_id,
            request_id=str(req_id),
            requester_id=requester_id,
            round=1,
            intent=ProcurementIntentSummary(
                request_id=str(req_id),
                requester_id=requester_id,
                department="Engineering",
                vendor_name="CrashResistant",
                estimated_spend=Decimal("60000.00"),
                annualized_spend=Decimal("60000.00"),
                currency="USD",
                justification="Resilience test",
            ),
            context=ApprovalContextSnapshot(
                snapshot_hash="h_crash_1",
                as_of="2026-11-13T00:00:00Z",
                citations=[
                    CitationFact(citation_id="c1", source_table="c", fact="f", as_of="2026-11-13")
                ],
                security_reviews=[
                    SecurityReviewSummary(
                        doc_type="SOC2",
                        status="compliant",
                        audit_period_end="2027-01-01",
                        is_fresh=True,
                    )
                ],
            ),
            policy=ApprovalPolicyRules(vp_spend_threshold={"USD": Decimal("50000.00")}),
            budget=BudgetSnapshot(
                department="Engineering",
                currency="USD",
                period="FY2026",
                amount=Decimal("100000.00"),
                committed=Decimal("0.00"),
            ),
            t1_seconds=1000,
            t2_seconds=2000,
            t3_seconds=3000,
        )

        wf_id = f"{tenant_id}:{req_id}"

        # 1. Run Worker 1 until workflow reaches awaiting_human
        # 1. Start Worker 1
        print("Starting Worker 1...", flush=True)
        async with Worker(
            env.client,
            task_queue="crash-q",
            workflows=APPROVAL_WORKFLOWS,
            activities=APPROVAL_ACTIVITIES,
        ) as w1:
            handle = await env.client.start_workflow(
                ProcurementApprovalWorkflow.run,
                inp.model_dump(mode="json"),
                id=wf_id,
                task_queue="crash-q",
            )

            # Wait until workflow posts Slack notification and enters awaiting_human
            session_factory = get_async_session_factory()
            for i in range(50):
                async with session_factory() as session:
                    ts = await session.scalar(
                        text(
                            "SELECT slack_message_ts FROM approval_steps WHERE tenant_id = :t AND request_id = :id AND slack_message_ts IS NOT NULL"
                        ),
                        {"t": tenant_id, "id": req_id},
                    )
                    if ts:
                        print(
                            f"Reached awaiting_human with Slack notification posted! ts={ts}",
                            flush=True,
                        )
                        break
                await asyncio.sleep(0.1)

        print("Worker 1 shut down cleanly.", flush=True)

        # 2. Spin up Worker 2 (resuming workflow)
        print("Starting Worker 2...", flush=True)
        async with Worker(
            env.client,
            task_queue="crash-q",
            workflows=APPROVAL_WORKFLOWS,
            activities=APPROVAL_ACTIVITIES,
        ) as w2:
            desc = await handle.describe()
            print(f"Workflow status on Worker 2 startup: {desc.status}", flush=True)

            auth_sig = HumanDecisionSignal(
                actor_id="vp_finance",
                step_name="finance_review",
                action="approve",
                idempotency_key=f"{tenant_id}:{req_id}:sig_resume",
            )
            print("Sending signal to Worker 2...", flush=True)
            await handle.signal(
                ProcurementApprovalWorkflow.signal_human_decision, auth_sig.model_dump(mode="json")
            )
            print("Signal sent, awaiting result...", flush=True)
            res = await handle.result()
            print("WORKFLOW COMPLETED UNDER WORKER 2:", res["status"], flush=True)


if __name__ == "__main__":
    asyncio.run(test_crash())
