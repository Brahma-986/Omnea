import asyncio
import os
import sys
from pathlib import Path
from decimal import Decimal

# Ensure workspace root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from workers.approval.workflows import ProcurementApprovalWorkflow
from tests.integration.test_phase4_workflow_engine import (
    APPROVAL_WORKFLOWS,
    APPROVAL_ACTIVITIES,
    _seed_test_data,
)
from workers.approval.models import (
    ApprovalWorkflowInput,
    ProcurementIntentSummary,
    ApprovalPolicyRules,
    BudgetSnapshot,
)
from procure_agents.approval.models import (
    ApprovalContextSnapshot,
    CitationFact,
    SecurityReviewSummary,
    ActiveContractSummary,
)
import uuid

async def run():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="rep-q",
            workflows=APPROVAL_WORKFLOWS,
            activities=APPROVAL_ACTIVITIES,
        ):
            t_id = f"t_rep_{uuid.uuid4().hex[:6]}"
            r_id = uuid.uuid4()
            await _seed_test_data(t_id, r_id, "rep_user")
            
            inp = ApprovalWorkflowInput(
                tenant_id=t_id,
                request_id=str(r_id),
                requester_id="rep_user",
                round=1,
                intent=ProcurementIntentSummary(
                    request_id=str(r_id),
                    requester_id="rep_user",
                    department="Engineering",
                    vendor_name="Acme Cloud",
                    estimated_spend=Decimal("5000.00"),
                    annualized_spend=Decimal("5000.00"),
                    currency="USD",
                    justification="Replay test",
                ),
                context=ApprovalContextSnapshot(
                    snapshot_hash="h_rep_1",
                    as_of="2026-11-13T00:00:00Z",
                    citations=[CitationFact(citation_id="c1", source_table="c", fact="f", as_of="2026-11-13")],
                    security_reviews=[SecurityReviewSummary(doc_type="SOC2", status="compliant", audit_period_end="2027-01-01", is_fresh=True)],
                    active_contracts=[ActiveContractSummary(contract_id="c1", title="A", total_value=Decimal("5000.00"), currency="USD", status="active", start_date="2025-01-01", end_date="2026-12-31")],
                ),
                policy=ApprovalPolicyRules(agent_autonomous_cap=Decimal("50000.00")),
                budget=BudgetSnapshot(department="Engineering", currency="USD", period="FY2026", amount=Decimal("100000.00"), committed=Decimal("0.00")),
            )
            handle = await env.client.start_workflow(
                ProcurementApprovalWorkflow.run,
                inp.model_dump(mode="json"),
                id=f"{t_id}:{r_id}",
                task_queue="rep-q",
            )
            res = await handle.result()
            print("Workflow result:", res["status"])
            hist = await handle.fetch_history()
            os.makedirs("tests/fixtures", exist_ok=True)
            with open("tests/fixtures/procurement_approval_workflow_history.json", "w", encoding="utf-8") as f:
                f.write(hist.to_json())
            print("Exported history successfully, size:", os.path.getsize("tests/fixtures/procurement_approval_workflow_history.json"))

if __name__ == "__main__":
    asyncio.run(run())
