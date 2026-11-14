"""Apply least privilege grant fixes for procure_ingest, procure_reviewer, procure_admin, and procure_workflow."""

import os
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

TEST_DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
TEST_DB_PORT = os.getenv("POSTGRES_PORT", "5432")
TEST_DB_NAME = os.getenv("POSTGRES_DB", "procure_os")
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")


async def main() -> None:
    admin_url = f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{TEST_DB_HOST}:{TEST_DB_PORT}/{TEST_DB_NAME}"
    engine = create_async_engine(admin_url)

    admin_pass = os.getenv("PROCURE_ADMIN_PASSWORD", "procure_admin_pass").replace("'", "''")
    wf_pass = os.getenv("PROCURE_WORKFLOW_PASSWORD", "procure_workflow_pass").replace("'", "''")
    rev_pass = os.getenv("PROCURE_REVIEWER_PASSWORD", "procure_reviewer_pass").replace("'", "''")
    ingest_pass = os.getenv("PROCURE_INGEST_PASSWORD", "procure_ingest_pass").replace("'", "''")

    sql_statements = [
        # 1. Update passwords per role
        f"ALTER ROLE procure_admin WITH PASSWORD '{admin_pass}';",
        f"ALTER ROLE procure_workflow WITH PASSWORD '{wf_pass}';",
        f"ALTER ROLE procure_reviewer WITH PASSWORD '{rev_pass}';",
        f"ALTER ROLE procure_ingest WITH PASSWORD '{ingest_pass}';",

        # 2. Drop procure_app role cleanly
        "DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'procure_app') THEN "
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM procure_app; "
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM procure_app; "
        "DROP ROLE procure_app; "
        "END IF; END $$;",

        # 3. Fix procure_ingest grants: vendors, aliases, embeddings, spend_transactions, requests.vendor_id
        "GRANT SELECT, INSERT, UPDATE ON vendors, vendor_aliases, vendor_embeddings, spend_transactions TO procure_ingest;",
        "GRANT SELECT, INSERT, UPDATE ON requests, procurement_intents, conversation_sessions, approvals, intent_vendor_resolutions, entity_review_tasks, neo4j_outbox TO procure_ingest;",
        "GRANT SELECT, INSERT ON resolution_decisions, audit_log TO procure_ingest;",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON inbound_event_queue TO procure_ingest;",
        "GRANT UPDATE (vendor_id) ON requests TO procure_ingest;",

        # 4. Drop reviewer read on internal queue/session/outbox tables
        "REVOKE SELECT ON inbound_event_queue, conversation_sessions, neo4j_outbox FROM procure_reviewer;",

        # 5. Revoke UPDATE, DELETE, TRUNCATE on audit_log from procure_admin
        "REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM procure_admin;",
        "REVOKE UPDATE, DELETE, TRUNCATE ON approval_audit_records FROM procure_admin;",
    ]

    async with engine.begin() as conn:
        for stmt in sql_statements:
            print(f"Executing: {stmt[:60]}...")
            await conn.execute(text(stmt))

    print("\nSuccessfully updated PostgreSQL roles and privileges.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
