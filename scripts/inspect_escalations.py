import json

with open("tests/eval/approval_cases.jsonl", encoding="utf-8") as f:
    cases = [json.loads(line) for line in f]

escalation_cases = [
    c
    for c in cases
    if c.get("must_not_auto_approve") or c.get("expected_final_status") == "awaiting_human"
]

fault_stubs = [c for c in escalation_cases if len(c.get("fault_injection", {})) > 0]
rule_derived = [c for c in escalation_cases if len(c.get("fault_injection", {})) == 0]

print(f"Total escalation cases: {len(escalation_cases)}")
print(f"Fault stubs count: {len(fault_stubs)}")
print(f"Rule derived count: {len(rule_derived)}\n")

print("=== 16 FAULT STUBS ===")
for i, c in enumerate(fault_stubs, 1):
    trigger = ", ".join(c.get("fault_injection", {}).keys())
    print(f"{i:2d}. {c['case_id']:35} | {c['category']:30} | Trigger: {trigger}")

print("\n=== RULE DERIVED ===")
categories = {}
for c in rule_derived:
    categories[c["category"]] = categories.get(c["category"], 0) + 1

for cat, count in categories.items():
    print(f"Category: {cat:35} -> {count} cases")
