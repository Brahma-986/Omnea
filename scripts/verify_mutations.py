"""Measured Mutation Testing Harness for Phase 4 Approval Engine.

Patches REAL production call sites on disk:
  1. activities.py line 455 (audit idempotency check)
  2. activities.py line 506 (outbox idempotency check)
  3. slack_interactions.py line 113 (authorize_approver call in Slack handler)
  4. verifier.py line 47 (rule citation verifier check)
  5. fallback.py line 98 (assigned approver & self-approval guard) -> tests test_03 AND test_09

For every mutant:
  1. Applies patch to real file on disk.
  2. Executes pytest via subprocess.
  3. Records exit code and failing test ID(s) (asserts failure).
  4. Restores original file on disk.
  5. Re-executes pytest via subprocess.
  6. Asserts exit code == 0 (PASSED).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


def extract_failing_tests(output: str) -> list[str]:
    """Parse pytest output to find failing test node IDs."""
    failures = []
    # Match FAILED path/to/file.py::test_name
    for line in output.splitlines():
        if line.startswith("FAILED "):
            test_id = line.split()[1].strip()
            failures.append(test_id)
    return failures


def run_mutation_test(
    name: str,
    file_path: Path,
    target_str: str,
    mutant_str: str,
    pytest_args: list[str],
) -> dict[str, str]:
    """Execute a single measured mutation test cycle against a real production call site."""
    print(f"\n================================================================================")
    print(f"RUNNING MUTANT: {name}")
    print(f"Target File: {file_path}")
    print(f"Target Code:\n  {target_str.strip()}")
    print(f"Mutated Code:\n  {mutant_str.strip()}")
    print(f"================================================================================")

    # 1. Read original content
    original_code = file_path.read_text(encoding="utf-8")
    if target_str not in original_code:
        raise RuntimeError(f"Target string not found in {file_path}:\n{target_str}")

    mutated_code = original_code.replace(target_str, mutant_str, 1)

    try:
        # 2. Write mutated code to real production file
        file_path.write_text(mutated_code, encoding="utf-8")

        # 3. Run test command against mutant in subprocess
        cmd = [sys.executable, "-m", "pytest"] + pytest_args
        res_mutant = subprocess.run(cmd, capture_output=True, text=True)

        mutant_exit_code = res_mutant.returncode
        failing_tests = extract_failing_tests(res_mutant.stdout + res_mutant.stderr)

        if mutant_exit_code == 0:
            raise AssertionError(f"Mutant {name} was NOT killed! Test unexpectedly passed.")

        fail_summary = ", ".join(failing_tests) if failing_tests else "FAILED (non-zero exit)"
        print(f"  DISABLED -> {fail_summary} (exit_code={mutant_exit_code})")

    finally:
        # 4. Always restore original code immediately
        file_path.write_text(original_code, encoding="utf-8")

    # 5. Run test command against restored code in subprocess
    res_restored = subprocess.run(cmd, capture_output=True, text=True)
    restored_exit_code = res_restored.returncode

    if restored_exit_code != 0:
        print(res_restored.stdout)
        print(res_restored.stderr)
        raise AssertionError(f"Original test failed after restore for {name}! (exit_code={restored_exit_code})")

    print(f"  RESTORED -> PASSED (exit_code=0)")

    return {
        "name": name,
        "disabled_result": fail_summary,
        "mutant_exit_code": str(mutant_exit_code),
        "restored_result": "PASSED",
        "restored_exit_code": "0",
    }


def main() -> None:
    root = Path(__file__).resolve().parent.parent

    mutations = [
        # Mutant 1: activities.py audit log idempotency check
        {
            "name": "Audit Idempotency Guard (activities.py:455)",
            "file_path": root / "services" / "workers" / "src" / "workers" / "approval" / "activities.py",
            "target_str": 'if await check_idempotency_seen(session, tenant_id, idempotency_key, "approval_audit_records"):',
            "mutant_str": 'if False and await check_idempotency_seen(session, tenant_id, idempotency_key, "approval_audit_records"):',
            "pytest_args": [
                "tests/integration/test_activities_idempotency.py",
                "-k",
                "test_audit_activity_idempotency_prevents_duplicate_insert",
                "-q",
            ],
        },
        # Mutant 2: activities.py outbox idempotency check
        {
            "name": "Outbox Idempotency Guard (activities.py:506)",
            "file_path": root / "services" / "workers" / "src" / "workers" / "approval" / "activities.py",
            "target_str": 'if await check_idempotency_seen(session, tenant_id, idempotency_key, "domain_events_outbox"):',
            "mutant_str": 'if False and await check_idempotency_seen(session, tenant_id, idempotency_key, "domain_events_outbox"):',
            "pytest_args": [
                "tests/integration/test_activities_idempotency.py",
                "-k",
                "test_outbox_activity_idempotency_prevents_duplicate_insert",
                "-q",
            ],
        },
        # Mutant 3: slack_interactions.py handler authorization call
        {
            "name": "Slack Handler Approver Authorization Call (slack_interactions.py:113)",
            "file_path": root / "services" / "workers" / "src" / "workers" / "approval" / "slack_interactions.py",
            "target_str": """    is_authorized = authorize_approver(
        actor_id=actor_id,
        requester_id=requester_id,
        assigned_approver_id=assigned_approver_id,
        is_admin=False,
    )""",
            "mutant_str": "    is_authorized = True  # MUTATED: bypass approver authorization",
            "pytest_args": [
                "tests/integration/test_slack_interactions_routes.py",
                "-k",
                "test_slack_route_unauthorized_self_approval_returns_403_and_audits_refusal",
                "-q",
            ],
        },
        # Mutant 4: verifier.py rule citation verifier
        {
            "name": "Citation Verifier Nonexistent Evidence ID Guard (verifier.py:47)",
            "file_path": root / "packages" / "procure_agents" / "src" / "procure_agents" / "approval" / "verifier.py",
            "target_str": "if eid not in valid_citations:",
            "mutant_str": "if False and eid not in valid_citations:",
            "pytest_args": [
                "tests/unit/test_approval_engine_core.py",
                "-k",
                "test_citation_verifier_nonexistent_evidence_id_degrades",
                "-q",
            ],
        },
        # Mutant 5: fallback.py assigned-approver & self-approval guard (testing BOTH test_03 and test_09)
        {
            "name": "Assigned Approver / Self-Approval Guard (fallback.py:98 -> test_03 & test_09)",
            "file_path": root / "packages" / "procure_agents" / "src" / "procure_agents" / "approval" / "fallback.py",
            "target_str": "    if actor_id == requester_id:\n        return False",
            "mutant_str": "    if actor_id == requester_id:\n        return True  # MUTATED: self-approval permitted",
            "pytest_args": [
                "tests/integration/test_phase4_workflow_engine.py",
                "-k",
                "test_03_human_signal_duplicate_and_unauthorized or test_09_self_approval_and_cross_tenant_rejected",
                "-q",
            ],
        },
    ]

    results = []
    for m in mutations:
        res = run_mutation_test(
            name=m["name"],
            file_path=m["file_path"],
            target_str=m["target_str"],
            mutant_str=m["mutant_str"],
            pytest_args=m["pytest_args"],
        )
        results.append(res)

    print("\n" + "=" * 80)
    print("ALL 5 MUTATION HARNESS RUNS MEASURED AND VERIFIED")
    print("=" * 80)
    for r in results:
        print(f"{r['name']}:")
        print(f"  disabled -> {r['disabled_result']} (exit {r['mutant_exit_code']})")
        print(f"  restored -> {r['restored_result']} (exit {r['restored_exit_code']})")


if __name__ == "__main__":
    main()
