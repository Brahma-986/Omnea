"""Split DB roles: workflow, read-only reviewer, ingest/resolver, admin.

Revision ID: 0006_split_db_roles
Revises: 0005_approval_engine
Create Date: 2026-11-13 15:30:00.000000

"""

from collections.abc import Sequence
import os

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_split_db_roles"
down_revision: str | None = "0005_approval_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    procure_pass = os.getenv("PROCURE_APP_PASSWORD")
    if not procure_pass:
        raise ValueError("Environment variable PROCURE_APP_PASSWORD must be explicitly set; no default permitted")
    procure_pass = procure_pass.replace("'", "''")

    op.execute(
        f"""
        -- 1. Create Roles if not existing
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'procure_admin') THEN
                CREATE ROLE procure_admin WITH LOGIN PASSWORD '{procure_pass}' SUPERUSER;
            END IF;
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'procure_workflow') THEN
                CREATE ROLE procure_workflow WITH LOGIN PASSWORD '{procure_pass}';
            END IF;
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'procure_reviewer') THEN
                CREATE ROLE procure_reviewer WITH LOGIN PASSWORD '{procure_pass}';
            END IF;
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'procure_ingest') THEN
                CREATE ROLE procure_ingest WITH LOGIN PASSWORD '{procure_pass}';
            END IF;
        END
        $$;

        -- 2. Schema USAGE
        GRANT USAGE ON SCHEMA public TO procure_workflow, procure_reviewer, procure_ingest;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO procure_workflow, procure_ingest;

        -- 3. procure_admin privileges
        GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO procure_admin;
        GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO procure_admin;

        -- 4. procure_workflow privileges (No INSERT/UPDATE on approver_roles, user_identities, contracts, vendor_security_reviews)
        -- No UPDATE on department_budgets.amount (column-level UPDATE on committed only)
        GRANT SELECT, INSERT, UPDATE ON requests, budget_reservations, approval_steps, domain_events_outbox TO procure_workflow;
        GRANT SELECT, INSERT ON approval_audit_records TO procure_workflow;
        GRANT SELECT ON approval_policies TO procure_workflow;
        GRANT SELECT ON approver_roles, user_identities, contracts, vendor_security_reviews TO procure_workflow;
        GRANT SELECT ON vendors, vendor_aliases, vendor_embeddings, spend_transactions, workspace_installations, tenant_resolution_settings TO procure_workflow;
        
        -- Column-level grant on department_budgets
        GRANT SELECT ON department_budgets TO procure_workflow;
        GRANT UPDATE (committed) ON department_budgets TO procure_workflow;

        -- Revoke write on protected tables from procure_workflow
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON approver_roles, user_identities, contracts, vendor_security_reviews FROM procure_workflow;
        REVOKE INSERT, DELETE, TRUNCATE ON department_budgets FROM procure_workflow;
        REVOKE UPDATE ON approval_audit_records, approval_policies FROM procure_workflow;
        REVOKE DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM procure_workflow;

        -- 5. procure_reviewer privileges (Read-Only Reviewer)
        GRANT SELECT ON ALL TABLES IN SCHEMA public TO procure_reviewer;
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM procure_reviewer;

        -- 6. procure_ingest privileges (Ingest & Intake Queue & Resolver)
        GRANT SELECT, INSERT, UPDATE, DELETE ON inbound_event_queue TO procure_ingest;
        GRANT SELECT, INSERT, UPDATE ON procurement_intents, conversation_sessions, approvals, intent_vendor_resolutions, entity_review_tasks, neo4j_outbox TO procure_ingest;
        GRANT SELECT, INSERT ON resolution_decisions, audit_log TO procure_ingest;
        GRANT SELECT ON vendors, vendor_aliases, vendor_embeddings, tenant_resolution_settings, workspace_installations TO procure_ingest;
        
        -- Explicitly revoke DELETE on neo4j_outbox and entity_review_tasks
        REVOKE DELETE, TRUNCATE ON neo4j_outbox, entity_review_tasks FROM procure_ingest, procure_workflow, public;
        REVOKE DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM procure_ingest;
        GRANT DELETE ON inbound_event_queue TO procure_ingest;

        -- 7. Wire procure_app role to inherit procure_workflow
        GRANT procure_workflow TO procure_app;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM procure_workflow, procure_reviewer, procure_ingest, procure_admin;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM procure_workflow, procure_ingest, procure_admin;
        REVOKE USAGE ON SCHEMA public FROM procure_workflow, procure_reviewer, procure_ingest;

        DROP OWNED BY procure_workflow;
        DROP ROLE IF EXISTS procure_workflow;

        DROP OWNED BY procure_reviewer;
        DROP ROLE IF EXISTS procure_reviewer;

        DROP OWNED BY procure_ingest;
        DROP ROLE IF EXISTS procure_ingest;

        DROP OWNED BY procure_admin;
        DROP ROLE IF EXISTS procure_admin;
        """
    )
