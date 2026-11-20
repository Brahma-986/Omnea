"""Split DB roles: workflow, read-only reviewer, ingest/resolver, admin.

Revision ID: 0006_split_db_roles
Revises: 0005_approval_engine
Create Date: 2026-11-13 15:30:00.000000

"""

import os
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_split_db_roles"
down_revision: str | None = "0005_approval_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    admin_pass = os.getenv("PROCURE_ADMIN_PASSWORD") or os.getenv(
        "PROCURE_APP_PASSWORD", "procure_admin_pass"
    )
    wf_pass = os.getenv("PROCURE_WORKFLOW_PASSWORD") or os.getenv(
        "PROCURE_APP_PASSWORD", "procure_workflow_pass"
    )
    rev_pass = os.getenv("PROCURE_REVIEWER_PASSWORD") or os.getenv(
        "PROCURE_APP_PASSWORD", "procure_reviewer_pass"
    )
    ingest_pass = os.getenv("PROCURE_INGEST_PASSWORD") or os.getenv(
        "PROCURE_APP_PASSWORD", "procure_ingest_pass"
    )

    admin_pass = admin_pass.replace("'", "''")
    wf_pass = wf_pass.replace("'", "''")
    rev_pass = rev_pass.replace("'", "''")
    ingest_pass = ingest_pass.replace("'", "''")

    op.execute(
        f"""
        -- 1. Create Roles if not existing (wrapped in exception block for managed cloud DBs)
        DO $$
        BEGIN
            BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'procure_admin') THEN
                    CREATE ROLE procure_admin WITH LOGIN PASSWORD '{admin_pass}' NOSUPERUSER;
                END IF;
            EXCEPTION WHEN insufficient_privilege OR duplicate_object THEN
                NULL;
            END;

            BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'procure_workflow') THEN
                    CREATE ROLE procure_workflow WITH LOGIN PASSWORD '{wf_pass}';
                END IF;
            EXCEPTION WHEN insufficient_privilege OR duplicate_object THEN
                NULL;
            END;

            BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'procure_reviewer') THEN
                    CREATE ROLE procure_reviewer WITH LOGIN PASSWORD '{rev_pass}';
                END IF;
            EXCEPTION WHEN insufficient_privilege OR duplicate_object THEN
                NULL;
            END;

            BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'procure_ingest') THEN
                    CREATE ROLE procure_ingest WITH LOGIN PASSWORD '{ingest_pass}';
                END IF;
            EXCEPTION WHEN insufficient_privilege OR duplicate_object THEN
                NULL;
            END;
        END
        $$;

        -- 2. Schema USAGE
        GRANT USAGE ON SCHEMA public TO procure_workflow, procure_reviewer, procure_ingest, procure_admin;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO procure_workflow, procure_ingest, procure_admin;

        -- 3. procure_admin privileges (No UPDATE/DELETE/TRUNCATE on audit tables)
        GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO procure_admin;
        REVOKE UPDATE, DELETE, TRUNCATE ON audit_log, approval_audit_records FROM procure_admin;

        -- 4. procure_workflow privileges (No INSERT/UPDATE on approver_roles, user_identities, contracts, vendor_security_reviews)
        -- No UPDATE on department_budgets.amount (column-level UPDATE on committed only)
        GRANT SELECT, INSERT, UPDATE ON requests, budget_reservations, approval_steps, domain_events_outbox TO procure_workflow;
        GRANT SELECT, INSERT ON approval_audit_records TO procure_workflow;
        GRANT SELECT ON approval_policies TO procure_workflow;
        GRANT SELECT ON approver_roles, user_identities, contracts, vendor_security_reviews TO procure_workflow;
        GRANT SELECT ON vendors, vendor_aliases, vendor_embeddings, spend_transactions, workspace_installations, tenant_resolution_settings TO procure_workflow;
        
        -- Column-level grant on department_budgets and requests
        GRANT SELECT ON department_budgets TO procure_workflow;
        GRANT UPDATE (committed) ON department_budgets TO procure_workflow;
        GRANT SELECT, INSERT ON requests TO procure_workflow;
        GRANT UPDATE (status, updated_at) ON requests TO procure_workflow;

        -- Revoke write on protected tables from procure_workflow
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON approver_roles, user_identities, contracts, vendor_security_reviews FROM procure_workflow;
        REVOKE INSERT, DELETE, TRUNCATE ON department_budgets FROM procure_workflow;
        REVOKE UPDATE ON approval_audit_records, approval_policies FROM procure_workflow;
        REVOKE DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM procure_workflow;

        -- 5. procure_reviewer privileges (Read-Only Reviewer: dropped approver_roles, user_identities, inbound_event_queue, conversation_sessions, neo4j_outbox)
        GRANT SELECT ON ALL TABLES IN SCHEMA public TO procure_reviewer;
        REVOKE SELECT ON approver_roles, user_identities, inbound_event_queue, conversation_sessions, neo4j_outbox FROM procure_reviewer;
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM procure_reviewer;

        -- 6. procure_ingest privileges (Ingest & Intake Queue & Resolver)
        GRANT SELECT, INSERT, UPDATE, DELETE ON inbound_event_queue TO procure_ingest;
        GRANT SELECT, INSERT, UPDATE ON procurement_intents, conversation_sessions, approvals, intent_vendor_resolutions, entity_review_tasks, neo4j_outbox TO procure_ingest;
        GRANT SELECT, INSERT, UPDATE ON vendors, vendor_aliases, vendor_embeddings, spend_transactions TO procure_ingest;
        GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_resolution_settings, workspace_installations TO procure_ingest;
        GRANT SELECT, INSERT ON resolution_decisions, audit_log TO procure_ingest;
        GRANT SELECT ON contracts, vendor_security_reviews, department_budgets TO procure_ingest;
        GRANT UPDATE (vendor_id) ON requests TO procure_ingest;
        
        -- Explicitly revoke DELETE on neo4j_outbox and entity_review_tasks
        REVOKE DELETE, TRUNCATE ON neo4j_outbox, entity_review_tasks FROM procure_ingest, procure_workflow, public;
        REVOKE DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM procure_ingest;
        GRANT DELETE ON inbound_event_queue TO procure_ingest;

        -- 7. Drop legacy procure_app role cleanly if permitted
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'procure_app') THEN
                BEGIN
                    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'postgres') THEN
                        BEGIN
                            ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM procure_app;
                            ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON SEQUENCES FROM procure_app;
                            ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM procure_app;
                        EXCEPTION WHEN OTHERS THEN
                            NULL;
                        END;
                    END IF;
                    REVOKE ALL PRIVILEGES ON SCHEMA public FROM procure_app;
                    REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM procure_app;
                    REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM procure_app;
                    REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM procure_app;
                    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'postgres') THEN
                        REASSIGN OWNED BY procure_app TO postgres;
                    END IF;
                    DROP OWNED BY procure_app;
                    DROP ROLE procure_app;
                EXCEPTION WHEN OTHERS THEN
                    NULL;
                END;
            END IF;
        END
        $$;
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
