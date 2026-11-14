"""Execute role migration and drop procure_app."""

import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main() -> None:
    engine = create_async_engine("postgresql+asyncpg://postgres:postgres@localhost:5432/procure_os")
    async with engine.connect() as conn:
        # Check if procure_app exists
        res = await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'procure_app'"))
        exists = res.scalar()
        if exists:
            stmts = [
                "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM procure_app",
                "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON SEQUENCES FROM procure_app",
                "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM procure_app",
                "REVOKE ALL PRIVILEGES ON SCHEMA public FROM procure_app",
                "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM procure_app",
                "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM procure_app",
                "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM procure_app",
                "REASSIGN OWNED BY procure_app TO postgres",
                "DROP OWNED BY procure_app",
                "DROP ROLE procure_app",
            ]
            for s in stmts:
                print(f"Running: {s}")
                await conn.execute(text(s))
            await conn.commit()
            print("procure_app dropped successfully!")
        else:
            print("procure_app does not exist.")

        # Apply grants
        grants = [
            "GRANT SELECT, INSERT, UPDATE ON vendors, vendor_aliases, vendor_embeddings, spend_transactions TO procure_ingest",
            "GRANT SELECT, INSERT, UPDATE ON requests, procurement_intents, conversation_sessions, approvals, intent_vendor_resolutions, entity_review_tasks, neo4j_outbox TO procure_ingest",
            "GRANT SELECT, INSERT ON resolution_decisions, audit_log TO procure_ingest",
            "GRANT SELECT, INSERT, UPDATE, DELETE ON inbound_event_queue TO procure_ingest",
            "GRANT UPDATE (vendor_id) ON requests TO procure_ingest",
            "REVOKE SELECT ON inbound_event_queue, conversation_sessions, neo4j_outbox FROM procure_reviewer",
            "REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM procure_admin",
            "REVOKE UPDATE, DELETE, TRUNCATE ON approval_audit_records FROM procure_admin",
        ]
        for g in grants:
            print(f"Running grant: {g}")
            await conn.execute(text(g))
        await conn.commit()
        print("All grants applied successfully!")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
