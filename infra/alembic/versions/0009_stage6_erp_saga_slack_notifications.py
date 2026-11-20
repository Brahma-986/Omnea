"""Stage 6: Systems of Record ERP, Idempotent Saga, Reconciliation, Slack App, Notifications.

Revision ID: 0009_stage6_erp_saga_slack
Revises: 0008_tprm_hardening
Create Date: 2026-11-19 23:35:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_stage6_erp_saga_slack"
down_revision: str | None = "0008_tprm_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        -- 1. ERP Configurations
        CREATE TABLE IF NOT EXISTS erp_configurations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL,
            connector_type VARCHAR(32) NOT NULL DEFAULT 'ledger',
            status VARCHAR(32) NOT NULL DEFAULT 'not_connected',
            secret_ref VARCHAR(255) NULL,
            settings JSONB NOT NULL DEFAULT '{}'::jsonb,
            last_sync_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_erp_config_tenant UNIQUE (tenant_id, connector_type)
        );
        CREATE INDEX IF NOT EXISTS idx_erp_config_tenant ON erp_configurations (tenant_id, status);

        -- 2. Built-in Ledger ERP Vendors
        CREATE TABLE IF NOT EXISTS erp_ledger_vendors (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL,
            erp_vendor_id VARCHAR(64) NOT NULL,
            name VARCHAR(255) NOT NULL,
            currency VARCHAR(3) NOT NULL DEFAULT 'USD',
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_erp_ledger_vendor UNIQUE (tenant_id, erp_vendor_id)
        );
        CREATE INDEX IF NOT EXISTS idx_erp_ledger_vendors_tenant ON erp_ledger_vendors (tenant_id);

        -- 3. Built-in Ledger ERP Purchase Orders
        CREATE TABLE IF NOT EXISTS erp_ledger_purchase_orders (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL,
            po_number VARCHAR(64) NOT NULL,
            request_id UUID NOT NULL,
            vendor_id VARCHAR(64) NOT NULL,
            vendor_name VARCHAR(255) NOT NULL,
            amount NUMERIC(18, 4) NOT NULL,
            currency VARCHAR(3) NOT NULL DEFAULT 'USD',
            status VARCHAR(32) NOT NULL DEFAULT 'open',
            line_items JSONB NOT NULL DEFAULT '[]'::jsonb,
            idempotency_key VARCHAR(128) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_erp_ledger_po_number UNIQUE (tenant_id, po_number),
            CONSTRAINT uq_erp_ledger_po_idempotency UNIQUE (tenant_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS idx_erp_ledger_po_tenant ON erp_ledger_purchase_orders (tenant_id, request_id);

        -- 4. Built-in Ledger ERP Commitments
        CREATE TABLE IF NOT EXISTS erp_ledger_commitments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL,
            po_id UUID NOT NULL REFERENCES erp_ledger_purchase_orders (id) ON DELETE CASCADE,
            po_number VARCHAR(64) NOT NULL,
            department VARCHAR(128) NOT NULL,
            amount NUMERIC(18, 4) NOT NULL,
            currency VARCHAR(3) NOT NULL DEFAULT 'USD',
            period VARCHAR(32) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'committed',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_erp_ledger_commitments_tenant ON erp_ledger_commitments (tenant_id, po_number);

        -- 5. ERP Saga Executions
        CREATE TABLE IF NOT EXISTS erp_saga_executions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL,
            request_id UUID NOT NULL,
            saga_id VARCHAR(128) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'started',
            current_step VARCHAR(64) NOT NULL DEFAULT 'init',
            compensation_step VARCHAR(64) NULL,
            idempotency_key VARCHAR(128) NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            po_number VARCHAR(64) NULL,
            error_detail TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_erp_saga_idempotency UNIQUE (tenant_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS idx_erp_saga_tenant_req ON erp_saga_executions (tenant_id, request_id);

        -- 6. ERP Reconciliation Runs
        CREATE TABLE IF NOT EXISTS erp_reconciliation_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'running',
            total_checked INT NOT NULL DEFAULT 0,
            drift_count INT NOT NULL DEFAULT 0,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ NULL
        );
        CREATE INDEX IF NOT EXISTS idx_erp_rec_runs_tenant ON erp_reconciliation_runs (tenant_id);

        -- 7. ERP Reconciliation Findings
        CREATE TABLE IF NOT EXISTS erp_reconciliation_findings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL,
            run_id UUID NOT NULL REFERENCES erp_reconciliation_runs (id) ON DELETE CASCADE,
            request_id UUID NULL,
            po_number VARCHAR(64) NULL,
            finding_type VARCHAR(64) NOT NULL,
            severity VARCHAR(32) NOT NULL DEFAULT 'medium',
            omnea_state JSONB NOT NULL DEFAULT '{}'::jsonb,
            erp_state JSONB NOT NULL DEFAULT '{}'::jsonb,
            is_resolved BOOLEAN NOT NULL DEFAULT FALSE,
            resolution_action VARCHAR(64) NULL,
            resolved_by VARCHAR(128) NULL,
            resolved_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_erp_rec_findings_tenant ON erp_reconciliation_findings (tenant_id, is_resolved);

        -- 8. ERP Reconciliation Repairs
        CREATE TABLE IF NOT EXISTS erp_reconciliation_repairs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL,
            finding_id UUID NOT NULL REFERENCES erp_reconciliation_findings (id) ON DELETE CASCADE,
            repair_action VARCHAR(64) NOT NULL,
            reason TEXT NOT NULL,
            actor_id VARCHAR(128) NOT NULL,
            audit_hash VARCHAR(64) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'completed',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_erp_repairs_tenant ON erp_reconciliation_repairs (tenant_id, finding_id);

        -- 9. Slack Installations
        CREATE TABLE IF NOT EXISTS slack_installations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL,
            team_id VARCHAR(64) NOT NULL,
            team_name VARCHAR(255) NOT NULL,
            bot_user_id VARCHAR(64) NOT NULL,
            secret_ref VARCHAR(255) NOT NULL,
            authed_user_id VARCHAR(64) NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_slack_install_tenant UNIQUE (tenant_id)
        );

        -- 10. Slack Identity Mappings
        CREATE TABLE IF NOT EXISTS slack_identity_mappings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL,
            slack_user_id VARCHAR(64) NOT NULL,
            omnea_user_id UUID NULL,
            email VARCHAR(255) NOT NULL,
            is_verified BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_slack_identity_tenant_user UNIQUE (tenant_id, slack_user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_slack_identity_email ON slack_identity_mappings (tenant_id, email);

        -- 11. User Notification Preferences
        CREATE TABLE IF NOT EXISTS user_notification_preferences (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL,
            user_id UUID NOT NULL,
            channels JSONB NOT NULL DEFAULT '{"in_app": true, "email": true, "slack": true}'::jsonb,
            event_subscriptions JSONB NOT NULL DEFAULT '{"request_submitted": true, "approval_requested": true, "request_approved": true, "request_rejected": true, "drift_detected": true}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_user_notif_pref UNIQUE (tenant_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_user_notif_pref ON user_notification_preferences (tenant_id, user_id);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS user_notification_preferences;
        DROP TABLE IF EXISTS slack_identity_mappings;
        DROP TABLE IF EXISTS slack_installations;
        DROP TABLE IF EXISTS erp_reconciliation_repairs;
        DROP TABLE IF EXISTS erp_reconciliation_findings;
        DROP TABLE IF EXISTS erp_reconciliation_runs;
        DROP TABLE IF EXISTS erp_saga_executions;
        DROP TABLE IF EXISTS erp_ledger_commitments;
        DROP TABLE IF EXISTS erp_ledger_purchase_orders;
        DROP TABLE IF EXISTS erp_ledger_vendors;
        DROP TABLE IF EXISTS erp_configurations;
        """
    )
