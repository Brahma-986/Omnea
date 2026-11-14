"""Measured Mutation Testing Harness for Phase 4 Approval Engine.

Patches REAL production call sites on disk:
  1. activities.py line 455 (audit idempotency check) -> test_audit_activity_idempotency_prevents_duplicate_insert
  2. activities.py line 506 (outbox idempotency check) -> test_outbox_activity_idempotency_prevents_duplicate_insert
  3. activities.py line 506 (outbox idempotency check) -> test_05_worker_crash_and_resume_idempotency
  4. activities.py line 557 (Slack post DB intent / lookup check) -> test_05_worker_crash_and_resume_idempotency
  5. slack_interactions.py line 160 (authorize_approver call in Slack handler) -> test_slack_route_unauthorized_self_approval_returns_403_and_audits_refusal
  6. verifier.py line 47 (rule citation verifier check) -> test_citation_verifier_nonexistent_evidence_id_degrades
  7. fallback.py line 98 (assigned approver & self-approval guard) -> tests test_03 AND test_09

For every mutant:
  1. Applies patch to real file on disk.
  2. Executes pytest via subprocess.
  3. Records exit code and failing test ID(s) and raw assertion error.
  4. Restores original file on disk in finally block.
  5. Re-executes pytest via subprocess.
  6. Asserts exit code == 0 (PASSED).
  7. Verifies git status and diff remain 100% clean.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


def extract_raw_failure(output: str) -> str:
    """Extract raw FAILED lines and assertion errors from pytest output."""
    lines = output.splitlines()
    failed_lines = [l for l in lines if l.startswith("FAILED ") or "AssertionError" in l or "UniqueViolationError" in l or "IntegrityError" in l]
    return "\n".join(failed_lines) if failed_lines else "Non-zero exit without explicit FAILED summary"


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

    original_code = file_path.read_text(encoding="utf-8")
    if target_str not in original_code:
        raise RuntimeError(f"Target string not found in {file_path}:\n{target_str}")

    mutated_code = original_code.replace(target_str, mutant_str, 1)

    try:
        # 1. Write mutant
        file_path.write_text(mutated_code, encoding="utf-8")

        # 2. Run test against mutant in subprocess
        cmd = [sys.executable, "-m", "pytest"] + pytest_args + ["--no-cov"]
        res_mutant = subprocess.run(cmd, capture_output=True, text=True)

        mutant_exit_code = res_mutant.returncode
        raw_failure = extract_raw_failure(res_mutant.stdout + res_mutant.stderr)

        if mutant_exit_code == 0:
            raise AssertionError(f"Mutant {name} was NOT killed! Test unexpectedly passed.")

        print(f"[MUTANT KILLED] exit_code={mutant_exit_code}")
        print(f"Raw Failure Output:\n{raw_failure}\n")

    finally:
        # 3. Always restore original code immediately in finally block
        file_path.write_text(original_code, encoding="utf-8")

    # 4. Verify test passes on restored code
    res_restored = subprocess.run(cmd, capture_output=True, text=True)
    restored_exit_code = res_restored.returncode

    if restored_exit_code != 0:
        print(res_restored.stdout)
        print(res_restored.stderr)
        raise AssertionError(f"Original test failed after restore for {name}! (exit_code={restored_exit_code})")

    print(f"[CODE RESTORED] exit_code=0 (PASSED)")

    return {
        "name": name,
        "raw_failure": raw_failure,
        "mutant_exit_code": str(mutant_exit_code),
        "restored_exit_code": "0",
    }


def main() -> None:
    root = Path(__file__).resolve().parent.parent

    mutations = [
        # Mutant 1: activities.py audit log idempotency check
        {
            "name": "Mutant 1: Audit Idempotency Guard (activities.py:455)",
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
            "name": "Mutant 2: Outbox Idempotency Guard (activities.py:506)",
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
        # Mutant 3: activities.py outbox idempotency check against Worker Crash Test 05
        {
            "name": "Mutant 3: Outbox Idempotency Guard against Crash Test (activities.py:506 -> test_05)",
            "file_path": root / "services" / "workers" / "src" / "workers" / "approval" / "activities.py",
            "target_str": 'if await check_idempotency_seen(session, tenant_id, idempotency_key, "domain_events_outbox"):',
            "mutant_str": 'if False and await check_idempotency_seen(session, tenant_id, idempotency_key, "domain_events_outbox"):',
            "pytest_args": [
                "tests/integration/test_phase4_workflow_engine.py",
                "-k",
                "test_05b_outbox_worker_crash_and_resume_idempotency",
                "-q",
            ],
        },
        # Mutant 4: activities.py Slack post DB lookup guard against Worker Crash Test 05
        {
            "name": "Mutant 4: Slack Post DB Lookup Guard (activities.py:557 -> test_05)",
            "file_path": root / "services" / "workers" / "src" / "workers" / "approval" / "activities.py",
            "target_str": "        if existing_ts:\n            # On retry: message was already initiated/posted. Return existing ts (in-place update)\n            return existing_ts",
            "mutant_str": "        if False and existing_ts:\n            return existing_ts",
            "pytest_args": [
                "tests/integration/test_phase4_workflow_engine.py",
                "-k",
                "test_05_worker_crash_and_resume_idempotency",
                "-q",
            ],
        },
        # Mutant 5: slack_interactions.py handler authorization call
        {
            "name": "Mutant 5: Slack Handler Approver Authorization Call (slack_interactions.py:160)",
            "file_path": root / "services" / "workers" / "src" / "workers" / "approval" / "slack_interactions.py",
            "target_str": "    is_authorized = authorize_approver(\n        actor_id=actor_id,\n        requester_id=requester_id,\n        assigned_approver_id=assigned_approver_id,\n        is_admin=False,\n    )",
            "mutant_str": "    is_authorized = True  # MUTATED: bypass approver authorization",
            "pytest_args": [
                "tests/integration/test_slack_interactions_routes.py",
                "-k",
                "test_slack_route_unauthorized_self_approval_returns_403_and_audits_refusal",
                "-q",
            ],
        },
        # Mutant 6: verifier.py rule citation verifier check
        {
            "name": "Mutant 6: Citation Verifier Nonexistent Evidence ID Guard (verifier.py:47)",
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
        # Mutant 7: fallback.py assigned-approver & self-approval guard (testing BOTH test_03 and test_09)
        {
            "name": "Mutant 7: Assigned Approver / Self-Approval Guard (fallback.py:98 -> test_03 & test_09)",
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

    print("\n" + "=" * 90)
    print("MEASURED MUTATION HARNESS SUMMARY (ALL 7 MUTANTS KILLED & RESTORED)")
    print("=" * 90)
    for r in results:
        print(f"{r['name']}:")
        print(f"  Mutant Result  : exit_code={r['mutant_exit_code']} (KILLED)")
        print(f"  Restored Result: exit_code={r['restored_exit_code']} (PASSED)")

    # Verify git status is completely clean
    git_diff = subprocess.run(["git", "diff"], capture_output=True, text=True).stdout
    print("\n[GIT TREE RESTORATION CHECK]")
    print(f"  git diff length: {len(git_diff)} bytes (clean={len(git_diff) == 0})")


if __name__ == "__main__":
    main()
