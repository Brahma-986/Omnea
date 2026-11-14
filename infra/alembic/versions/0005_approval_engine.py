"""Approval engine tables, status constraints, audit immutability, reservations, and outbox.

Revision ID: 0005_approval_engine
Revises: 0004_enterprise_context
Create Date: 2026-11-13 13:00:00.000000

"""

from collections.abc import Sequence
import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "0005_approval_engine"
down_revision: str | None = "0004_enterprise_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # =========================================================================
    # 1. Update requests status constraints & transition trigger
    # =========================================================================
    op.create_check_constraint(
        "chk_request_status",
        "requests",
        "status IN ('draft', 'awaiting_entity_review', 'in_review', 'awaiting_human', "
        "'approved', 'rejected', 'expired', 'cancelled', 'failed')",
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION check_request_status_transition()
        RETURNS TRIGGER AS $$
        BEGIN
            -- Block INSERT with non-draft status
            IF TG_OP = 'INSERT' THEN
                IF NEW.status != 'draft' THEN
                    RAISE EXCEPTION
                        'New requests must be inserted with status draft, got: %', NEW.status;
                END IF;
                RETURN NEW;
            END IF;

            -- Handle UPDATE
            IF OLD.status = NEW.status THEN
                RETURN NEW;
            END IF;

            -- Legal transitions:
            -- draft -> awaiting_entity_review, in_review, cancelled, failed
            IF OLD.status = 'draft' AND NEW.status IN (
                'awaiting_entity_review', 'in_review', 'cancelled', 'failed'
            ) THEN
                RETURN NEW;
            -- awaiting_entity_review -> in_review, expired, cancelled, failed
            ELSIF OLD.status = 'awaiting_entity_review' AND NEW.status IN (
                'in_review', 'expired', 'cancelled', 'failed'
            ) THEN
                RETURN NEW;
            -- in_review -> awaiting_human, approved, rejected, cancelled, failed
            ELSIF OLD.status = 'in_review' AND NEW.status IN (
                'awaiting_human', 'approved', 'rejected', 'cancelled', 'failed'
            ) THEN
                RETURN NEW;
            -- awaiting_human -> in_review (request info/refresh), approved, rejected, etc.
            ELSIF OLD.status = 'awaiting_human' AND NEW.status IN (
                'in_review', 'approved', 'rejected', 'expired', 'cancelled', 'failed'
            ) THEN
                RETURN NEW;
            -- expired -> in_review (resubmission on new round)
            ELSIF OLD.status = 'expired' AND NEW.status = 'in_review' THEN
                RETURN NEW;
            ELSE
                RAISE EXCEPTION
                    'Illegal request status transition from % to %', OLD.status, NEW.status;
            END IF;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_request_status_transition
        BEFORE INSERT OR UPDATE OF status ON requests
        FOR EACH ROW
        EXECUTE FUNCTION check_request_status_transition();
        """
    )

    # Relate/deprecate Phase 0's approvals table: alter request_id FK to RESTRICT
    op.drop_constraint("approvals_request_id_fkey", "approvals", type_="foreignkey")
    op.create_foreign_key(
        "fk_approvals_request_restrict",
        "approvals",
        "requests",
        ["request_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # =========================================================================
    # 2. Table: approval_policies (Immutable per version + content_hash)
    # =========================================================================
    op.create_table(
        "approval_policies",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("version", sa.String(32), nullable=False, server_default="v1.0"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("rules", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.UniqueConstraint("tenant_id", "version", name="uq_approval_policy_version"),
    )
    # Partial unique index: strictly one active policy per tenant
    op.execute(
        """
        CREATE UNIQUE INDEX uq_active_policy_per_tenant
        ON approval_policies (tenant_id)
        WHERE is_active = true;
        """
    )
    op.create_index("ix_approval_policies_tenant", "approval_policies", ["tenant_id", "is_active"])

    # Immutability trigger for approval_policies: only is_active may flip, rules/version/content_hash immutable
    op.execute(
        """
        CREATE OR REPLACE FUNCTION check_approval_policy_immutable()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'approval_policies is immutable: delete prohibited';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                IF OLD.tenant_id != NEW.tenant_id
                   OR OLD.version != NEW.version
                   OR OLD.content_hash != NEW.content_hash
                   OR OLD.rules != NEW.rules THEN
                    RAISE EXCEPTION 'approval_policies is immutable: only is_active may be modified';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_approval_policy_immutable
        BEFORE UPDATE OR DELETE ON approval_policies
        FOR EACH ROW
        EXECUTE FUNCTION check_approval_policy_immutable();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_approval_policy_truncate
        BEFORE TRUNCATE ON approval_policies
        FOR EACH STATEMENT
        EXECUTE FUNCTION check_approval_policy_immutable();
        """
    )

    # =========================================================================
    # 3. Table: department_budgets
    # =========================================================================
    op.create_table(
        "department_budgets",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("department", sa.String(128), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("period", sa.String(32), nullable=False),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("committed", sa.Numeric(18, 4), nullable=False, server_default="0.0000"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.UniqueConstraint(
            "tenant_id", "department", "currency", "period", name="uq_dept_budget_period"
        ),
        sa.CheckConstraint("amount >= 0", name="chk_dept_budget_positive"),
        sa.CheckConstraint("committed >= 0", name="chk_dept_budget_committed"),
        sa.CheckConstraint("committed <= amount", name="chk_dept_budget_committed_le_amount"),
    )
    op.create_index(
        "ix_dept_budgets_lookup", "department_budgets", ["tenant_id", "department", "currency"]
    )

    # =========================================================================
    # 4. Table: budget_reservations
    # =========================================================================
    op.create_table(
        "budget_reservations",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("request_id", UUID(as_uuid=True), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("department", sa.String(128), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("period", sa.String(32), nullable=False),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_budget_reservation_idempotency"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            ["requests.tenant_id", "requests.id"],
            name="fk_budget_res_request",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'committed', 'released')", name="chk_budget_res_status"
        ),
        sa.CheckConstraint("amount >= 0", name="chk_budget_res_amount"),
    )
    op.create_index(
        "ix_budget_reservations_lookup",
        "budget_reservations",
        ["tenant_id", "department", "currency", "period", "status"],
    )
    # Strictly one active reservation per (tenant, request, round)
    op.execute(
        """
        CREATE UNIQUE INDEX uq_active_reservation_per_request_round
        ON budget_reservations (tenant_id, request_id, round)
        WHERE status = 'active';
        """
    )

    # =========================================================================
    # 5. Table: approver_roles (with delegate validity window)
    # =========================================================================
    op.create_table(
        "approver_roles",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("delegate_id", sa.String(128), nullable=True),
        sa.Column("delegate_valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delegate_valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("backup_id", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.UniqueConstraint("tenant_id", "role", name="uq_approver_role"),
        sa.CheckConstraint(
            "(delegate_id IS NULL AND delegate_valid_from IS NULL AND delegate_valid_until IS NULL) OR "
            "(delegate_id IS NOT NULL AND delegate_valid_from IS NOT NULL AND delegate_valid_until IS NOT NULL AND delegate_valid_from < delegate_valid_until)",
            name="chk_approver_delegate_window",
        ),
    )
    op.create_index("ix_approver_roles_tenant", "approver_roles", ["tenant_id", "role"])

    # =========================================================================
    # 6. Table: user_identities
    # =========================================================================
    op.create_table(
        "user_identities",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("external_user_id", sa.String(128), nullable=False),
        sa.Column("internal_user_id", sa.String(128), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.UniqueConstraint(
            "tenant_id", "provider", "external_user_id", name="uq_user_identity_external"
        ),
    )
    op.create_index(
        "ix_user_identities_lookup",
        "user_identities",
        ["tenant_id", "provider", "external_user_id"],
    )

    # =========================================================================
    # 7. Table: approval_steps
    # =========================================================================
    op.create_table(
        "approval_steps",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("request_id", UUID(as_uuid=True), nullable=False),
        sa.Column("step_name", sa.String(64), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reviewer_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("assignee", sa.String(128), nullable=True),
        sa.Column("escalated_to", sa.String(128), nullable=True),
        sa.Column("slack_message_ts", sa.String(64), nullable=True),
        sa.Column("t1_reminder_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("t2_escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("t3_expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("agent_verdict", JSONB, nullable=True),
        sa.Column("human_verdict", JSONB, nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.UniqueConstraint(
            "tenant_id", "request_id", "step_name", "round", name="uq_approval_step_unique"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            ["requests.tenant_id", "requests.id"],
            name="fk_approval_steps_request",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "reviewer_type IN ('agent', 'human')", name="chk_approval_step_reviewer_type"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'needs_human', 'awaiting_human', "
            "'approved', 'rejected', 'skipped', 'expired')",
            name="chk_approval_step_status",
        ),
    )
    op.create_index("ix_approval_steps_request", "approval_steps", ["tenant_id", "request_id"])

    # Terminal status immutability trigger for approval_steps
    op.execute(
        """
        CREATE OR REPLACE FUNCTION check_approval_step_terminal_status()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF OLD.status IN ('approved', 'rejected', 'skipped', 'expired')
                   AND OLD.status != NEW.status THEN
                    RAISE EXCEPTION
                        'Terminal approval step status % cannot be changed to %',
                        OLD.status, NEW.status;
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_approval_step_terminal
        BEFORE UPDATE OF status ON approval_steps
        FOR EACH ROW
        EXECUTE FUNCTION check_approval_step_terminal_status();
        """
    )

    # =========================================================================
    # 8. Table: approval_audit_records (Append-Only with Sequence & Clock)
    # =========================================================================
    op.create_table(
        "approval_audit_records",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column(
            "sequence",
            sa.BigInteger(),
            sa.Identity(start=1, cycle=False),
            nullable=False,
        ),
        sa.Column("request_id", UUID(as_uuid=True), nullable=False),
        sa.Column("step_id", UUID(as_uuid=True), nullable=True),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("verdict", sa.String(32), nullable=True),
        sa.Column("prompt_version", sa.String(32), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("policy_version", sa.String(32), nullable=True),
        sa.Column("context_snapshot_hash", sa.String(64), nullable=True),
        sa.Column("reasons", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("evidence_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("slack_message_ts", sa.String(64), nullable=True),
        sa.Column("signal_idempotency_key", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            ["requests.tenant_id", "requests.id"],
            name="fk_audit_request",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_audit_idempotency"),
        sa.CheckConstraint(
            "actor_type IN ('agent', 'human', 'system')", name="chk_audit_actor_type"
        ),
        sa.CheckConstraint(
            "verdict IS NULL OR verdict IN ('approve', 'reject', 'needs_human')",
            name="chk_audit_verdict",
        ),
    )
    op.create_index(
        "ix_approval_audit_req",
        "approval_audit_records",
        ["tenant_id", "request_id", "sequence"],
    )

    # Immutability triggers: Block UPDATE, DELETE, and TRUNCATE on approval_audit_records
    op.execute(
        """
        CREATE OR REPLACE FUNCTION block_audit_record_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'approval_audit_records is append-only: mutations are prohibited';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_block_audit_mutation
        BEFORE UPDATE OR DELETE ON approval_audit_records
        FOR EACH ROW
        EXECUTE FUNCTION block_audit_record_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_block_audit_truncate
        BEFORE TRUNCATE ON approval_audit_records
        FOR EACH STATEMENT
        EXECUTE FUNCTION block_audit_record_mutation();
        """
    )

    # =========================================================================
    # 9. Table: domain_events_outbox
    # =========================================================================
    op.create_table(
        "domain_events_outbox",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column(
            "sequence",
            sa.BigInteger(),
            sa.Identity(start=1, cycle=False),
            nullable=False,
        ),
        sa.Column(
            "aggregate_type",
            sa.String(64),
            nullable=False,
            server_default="procurement_request",
        ),
        sa.Column("aggregate_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_domain_event_idempotency"),
        sa.CheckConstraint(
            "status IN ('pending', 'published', 'dead_letter')", name="chk_domain_event_status"
        ),
    )
    op.create_index(
        "ix_domain_events_pending",
        "domain_events_outbox",
        ["tenant_id", "status", "next_attempt_at", "sequence"],
    )

    # Configure app role procure_app with explicit least-privilege table grants
    procure_pass = os.getenv("PROCURE_APP_PASSWORD")
    if not procure_pass:
        raise ValueError("Environment variable PROCURE_APP_PASSWORD must be explicitly set; no default permitted")
    procure_pass = procure_pass.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'procure_app') THEN
                CREATE ROLE procure_app WITH LOGIN PASSWORD '{procure_pass}';
            END IF;
        END
        $$;
        GRANT USAGE ON SCHEMA public TO procure_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO procure_app;

        -- Explicit table-level least-privilege grants
        GRANT SELECT, INSERT, UPDATE ON requests, budget_reservations, approval_steps, domain_events_outbox TO procure_app;
        GRANT SELECT, INSERT, UPDATE ON department_budgets, approver_roles, user_identities TO procure_app;
        GRANT SELECT ON approval_policies TO procure_app;
        GRANT SELECT, INSERT ON approval_audit_records TO procure_app;

        -- Phase 3 & base tables grants
        GRANT SELECT, INSERT, UPDATE, DELETE ON entity_review_tasks, neo4j_outbox, procurement_intents, inbound_event_queue, conversation_sessions, approvals, intent_vendor_resolutions TO procure_app;
        GRANT SELECT, INSERT, UPDATE ON vendors, vendor_aliases, vendor_embeddings, contracts, spend_transactions, vendor_security_reviews, tenant_resolution_settings, workspace_installations TO procure_app;
        GRANT SELECT, INSERT ON resolution_decisions, audit_log TO procure_app;

        -- Explicit least-privilege revocations
        REVOKE DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM procure_app;
        GRANT DELETE ON inbound_event_queue, entity_review_tasks, neo4j_outbox, intent_vendor_resolutions TO procure_app;
        REVOKE UPDATE ON approval_audit_records, audit_log, resolution_decisions, approval_policies FROM procure_app;
        REVOKE DELETE, INSERT ON approval_policies FROM procure_app;

        -- Alter default privileges to ensure new tables default to SELECT only
        ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO procure_app;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'procure_app') THEN
                ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM procure_app;
                ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON SEQUENCES FROM procure_app;
                ALTER DEFAULT PRIVILEGES REVOKE ALL ON TABLES FROM procure_app;
                ALTER DEFAULT PRIVILEGES REVOKE ALL ON SEQUENCES FROM procure_app;
                DROP OWNED BY procure_app;
                REVOKE ALL ON ALL TABLES IN SCHEMA public FROM procure_app;
                REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM procure_app;
                REVOKE USAGE ON SCHEMA public FROM procure_app;
                DROP ROLE procure_app;
            END IF;
        END
        $$;
        """
    )

    op.drop_table("domain_events_outbox")

    op.execute("DROP TRIGGER IF EXISTS trg_block_audit_truncate ON approval_audit_records;")
    op.execute("DROP TRIGGER IF EXISTS trg_block_audit_mutation ON approval_audit_records;")
    op.execute("DROP FUNCTION IF EXISTS block_audit_record_mutation();")
    op.drop_table("approval_audit_records")

    op.execute("DROP TRIGGER IF EXISTS trg_approval_step_terminal ON approval_steps;")
    op.execute("DROP FUNCTION IF EXISTS check_approval_step_terminal_status();")
    op.drop_table("approval_steps")

    op.drop_table("user_identities")
    op.drop_table("approver_roles")

    op.execute("DROP INDEX IF EXISTS uq_active_reservation_per_request_round;")
    op.drop_table("budget_reservations")

    op.drop_table("department_budgets")

    op.execute("DROP TRIGGER IF EXISTS trg_approval_policy_truncate ON approval_policies;")
    op.execute("DROP TRIGGER IF EXISTS trg_approval_policy_immutable ON approval_policies;")
    op.execute("DROP FUNCTION IF EXISTS check_approval_policy_immutable();")
    op.execute("DROP INDEX IF EXISTS uq_active_policy_per_tenant;")
    op.drop_table("approval_policies")

    # Restore Phase 0 approvals table FK
    op.drop_constraint("fk_approvals_request_restrict", "approvals", type_="foreignkey")
    op.create_foreign_key(
        "approvals_request_id_fkey",
        "approvals",
        "requests",
        ["request_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.execute("DROP TRIGGER IF EXISTS trg_request_status_transition ON requests;")
    op.execute("DROP FUNCTION IF EXISTS check_request_status_transition();")
    op.drop_constraint("chk_request_status", "requests", type_="check")
