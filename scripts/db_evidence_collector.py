"""Script to collect exact database evidence for Item 5."""

import hashlib
import json
import uuid

import psycopg


def run_checks() -> None:
    conn = psycopg.connect(
        "postgresql://postgres:postgres@localhost:5432/procure_os", autocommit=True
    )
    cur = conn.cursor()

    print("=== 1. ROLES (\\du) ===")
    cur.execute(
        "SELECT rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin FROM pg_roles WHERE rolname IN ('postgres', 'procure_app');"
    )
    for r in cur.fetchall():
        print(f"Role: {r[0]}, Super: {r[1]}, Login: {r[5]}")

    print("\n=== 2. GRANTS FOR procure_app ===")
    cur.execute("""
        SELECT table_name, privilege_type 
        FROM information_schema.table_privileges 
        WHERE grantee = 'procure_app' AND table_name IN ('approval_policies', 'approval_steps', 'approval_audit_records')
        ORDER BY table_name, privilege_type;
    """)
    for r in cur.fetchall():
        print(f"Table: {r[0]:25s} | Privilege: {r[1]}")

    print("\n=== 3. IMMUTABILITY AS procure_app ON approval_audit_records ===")
    req_id = uuid.uuid4()
    audit_id = uuid.uuid4()
    cur.execute(
        f"INSERT INTO requests (id, tenant_id, requester_id, status) VALUES ('{req_id}', 't_app', 'u1', 'draft');"
    )
    cur.execute(
        f"INSERT INTO approval_audit_records (id, tenant_id, request_id, actor_type, actor_id, action, idempotency_key) VALUES ('{audit_id}', 't_app', '{req_id}', 'agent', 'mod', 'act', 'idem_{uuid.uuid4()}');"
    )

    # Connect as procure_app
    app_conn = psycopg.connect(
        "postgresql://procure_app:procure_app_pass@localhost:5432/procure_os", autocommit=False
    )
    app_cur = app_conn.cursor()

    # UPDATE attempt
    try:
        app_cur.execute(
            f"UPDATE approval_audit_records SET action = 'tampered' WHERE id = '{audit_id}';"
        )
        app_conn.commit()
    except Exception as e:
        app_conn.rollback()
        print(f"UPDATE as procure_app refused: {type(e).__name__} - {e}".strip())

    # DELETE attempt
    try:
        app_cur.execute(f"DELETE FROM approval_audit_records WHERE id = '{audit_id}';")
        app_conn.commit()
    except Exception as e:
        app_conn.rollback()
        print(f"DELETE as procure_app refused: {type(e).__name__} - {e}".strip())

    # TRUNCATE attempt
    try:
        app_cur.execute("TRUNCATE TABLE approval_audit_records;")
        app_conn.commit()
    except Exception as e:
        app_conn.rollback()
        print(f"TRUNCATE as procure_app refused: {type(e).__name__} - {e}".strip())

    app_conn.close()

    print("\n=== 4. APPROVAL POLICIES: FAILING RULES UPDATE & PASSING is_active FLIP ===")
    pol_id = uuid.uuid4()
    rules = {"vp_threshold": {"USD": 50000}}
    rules_json = json.dumps(rules, sort_keys=True)
    content_hash = hashlib.sha256(rules_json.encode()).hexdigest()

    cur.execute(
        f"INSERT INTO approval_policies (id, tenant_id, version, content_hash, rules, is_active) VALUES ('{pol_id}', 't_app', 'v1.0', '{content_hash}', '{rules_json}'::jsonb, true);"
    )

    # 4a. Passing is_active flip
    cur.execute(f"UPDATE approval_policies SET is_active = false WHERE id = '{pol_id}';")
    cur.execute(f"SELECT is_active FROM approval_policies WHERE id = '{pol_id}';")
    print(f"Passing is_active flip verified: is_active = {cur.fetchone()[0]}")

    # 4b. Failing rules UPDATE
    try:
        cur.execute(
            f"UPDATE approval_policies SET rules = '{{\"vp_threshold\": {{\"USD\": 999999}}}}'::jsonb WHERE id = '{pol_id}';"
        )
    except Exception as e:
        print(f"Failing rules UPDATE refused: {e}".strip())

    print("\n=== 5. TERMINAL STEP approved -> rejected REFUSED ===")
    step_id = uuid.uuid4()
    cur.execute(
        f"INSERT INTO approval_steps (id, tenant_id, request_id, step_name, round, reviewer_type, status) VALUES ('{step_id}', 't_app', '{req_id}', 'sec', 1, 'agent', 'approved');"
    )
    try:
        cur.execute(f"UPDATE approval_steps SET status = 'rejected' WHERE id = '{step_id}';")
    except Exception as e:
        print(f"Terminal step transition refused: {e}".strip())

    print("\n=== 6. DIFFERENT IDEMPOTENCY KEY DOUBLE ACTIVE RESERVATION REFUSED ===")
    res1_id = uuid.uuid4()
    res2_id = uuid.uuid4()
    cur.execute(
        f"INSERT INTO budget_reservations (id, tenant_id, request_id, round, department, currency, period, amount, status, idempotency_key) VALUES ('{res1_id}', 't_app', '{req_id}', 1, 'Eng', 'USD', 'FY2026', 100.0, 'active', 'idem_key_AAA');"
    )
    try:
        # Different idempotency key, but same (tenant_id, request_id, round) with status='active'
        cur.execute(
            f"INSERT INTO budget_reservations (id, tenant_id, request_id, round, department, currency, period, amount, status, idempotency_key) VALUES ('{res2_id}', 't_app', '{req_id}', 1, 'Eng', 'USD', 'FY2026', 200.0, 'active', 'idem_key_BBB');"
        )
    except Exception as e:
        print(f"Double active reservation refused: {e}".strip())

    print("\n=== 7. CONTENT_HASH VERIFIED AGAINST RULES ===")
    cur.execute(f"SELECT content_hash, rules FROM approval_policies WHERE id = '{pol_id}';")
    db_hash, db_rules = cur.fetchone()
    computed_hash = hashlib.sha256(json.dumps(db_rules, sort_keys=True).encode()).hexdigest()
    print(f"DB content_hash      : {db_hash}")
    print(f"Computed SHA256(rules): {computed_hash}")
    print(f"Content hash match   : {db_hash == computed_hash}")

    conn.close()


if __name__ == "__main__":
    run_checks()
