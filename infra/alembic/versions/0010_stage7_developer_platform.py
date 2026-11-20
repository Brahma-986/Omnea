"""Stage 7: Developer platform - API Keys, Webhooks, Idempotency, and RLS.

Revision ID: 0010_stage7_developer_platform
Revises: 0009_stage6_erp_saga_slack
Create Date: 2026-11-20 00:40:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_stage7_developer_platform"
down_revision: str | None = "0009_stage6_erp_saga_slack"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        -- 1. API Keys table & columns
        CREATE TABLE IF NOT EXISTS api_keys (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL,
            name VARCHAR(255) NOT NULL,
            key_prefix VARCHAR(32) NOT NULL,
            key_hash VARCHAR(128) NOT NULL UNIQUE,
            scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
            rate_limit_per_minute INTEGER NOT NULL DEFAULT 60,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMPTZ NULL,
            revoked_at TIMESTAMPTZ NULL,
            last_used_at TIMESTAMPTZ NULL,
            created_by VARCHAR(255) NOT NULL DEFAULT 'system'
        );
        ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ NULL;
        ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS created_by VARCHAR(255) NOT NULL DEFAULT 'system';
        ALTER TABLE api_keys ALTER COLUMN key_prefix TYPE VARCHAR(32);
        CREATE INDEX IF NOT EXISTS idx_api_keys_tenant ON api_keys (tenant_id);
        CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys (key_hash);

        -- 2. Webhook Subscriptions (Endpoints) table & columns
        CREATE TABLE IF NOT EXISTS webhook_subscriptions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL,
            url VARCHAR(1024) NOT NULL,
            secret VARCHAR(255) NOT NULL,
            description VARCHAR(255) NOT NULL DEFAULT '',
            events JSONB NOT NULL DEFAULT '["*"]'::jsonb,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        ALTER TABLE webhook_subscriptions ADD COLUMN IF NOT EXISTS description VARCHAR(255) NOT NULL DEFAULT '';
        CREATE INDEX IF NOT EXISTS idx_webhook_subscriptions_tenant ON webhook_subscriptions (tenant_id);

        -- 3. Webhook Deliveries table & columns
        CREATE TABLE IF NOT EXISTS webhook_deliveries (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL,
            subscription_id UUID NOT NULL,
            event_type VARCHAR(64) NOT NULL,
            event_id VARCHAR(64) NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            response_status INTEGER NULL,
            response_body TEXT NULL,
            attempt INTEGER NOT NULL DEFAULT 1,
            success BOOLEAN NOT NULL DEFAULT FALSE,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            delivered_at TIMESTAMPTZ NULL
        );
        ALTER TABLE webhook_deliveries ADD COLUMN IF NOT EXISTS event_id VARCHAR(64) NULL;
        ALTER TABLE webhook_deliveries ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'pending';
        ALTER TABLE webhook_deliveries ADD COLUMN IF NOT EXISTS duration_ms INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE webhook_deliveries ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
        ALTER TABLE webhook_deliveries ALTER COLUMN delivered_at DROP NOT NULL;

        CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_tenant ON webhook_deliveries (tenant_id);
        CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_sub ON webhook_deliveries (subscription_id);
        CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_created ON webhook_deliveries (created_at DESC);

        -- 4. API Idempotency Records table
        CREATE TABLE IF NOT EXISTS api_idempotency_records (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL,
            idempotency_key VARCHAR(255) NOT NULL,
            request_path VARCHAR(255) NOT NULL,
            request_method VARCHAR(16) NOT NULL,
            response_status_code INTEGER NOT NULL,
            response_body JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT uq_api_idempotency_tenant_key UNIQUE (tenant_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS idx_api_idempotency_lookup ON api_idempotency_records (tenant_id, idempotency_key);

        -- 5. Enable and force Row Level Security (RLS)
        ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
        ALTER TABLE api_keys FORCE ROW LEVEL SECURITY;
        ALTER TABLE webhook_subscriptions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE webhook_subscriptions FORCE ROW LEVEL SECURITY;
        ALTER TABLE webhook_deliveries ENABLE ROW LEVEL SECURITY;
        ALTER TABLE webhook_deliveries FORCE ROW LEVEL SECURITY;
        ALTER TABLE api_idempotency_records ENABLE ROW LEVEL SECURITY;
        ALTER TABLE api_idempotency_records FORCE ROW LEVEL SECURITY;

        -- 6. Tenant isolation policies
        DROP POLICY IF EXISTS api_keys_tenant_isolation ON api_keys;
        CREATE POLICY api_keys_tenant_isolation ON api_keys
            AS PERMISSIVE FOR ALL
            USING (
                CURRENT_USER = 'procure_admin'
                OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
            )
            WITH CHECK (
                CURRENT_USER = 'procure_admin'
                OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
            );

        DROP POLICY IF EXISTS webhook_subscriptions_tenant_isolation ON webhook_subscriptions;
        CREATE POLICY webhook_subscriptions_tenant_isolation ON webhook_subscriptions
            AS PERMISSIVE FOR ALL
            USING (
                CURRENT_USER = 'procure_admin'
                OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
            )
            WITH CHECK (
                CURRENT_USER = 'procure_admin'
                OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
            );

        DROP POLICY IF EXISTS webhook_deliveries_tenant_isolation ON webhook_deliveries;
        CREATE POLICY webhook_deliveries_tenant_isolation ON webhook_deliveries
            AS PERMISSIVE FOR ALL
            USING (
                CURRENT_USER = 'procure_admin'
                OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
            )
            WITH CHECK (
                CURRENT_USER = 'procure_admin'
                OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
            );

        DROP POLICY IF EXISTS api_idempotency_tenant_isolation ON api_idempotency_records;
        CREATE POLICY api_idempotency_tenant_isolation ON api_idempotency_records
            AS PERMISSIVE FOR ALL
            USING (
                CURRENT_USER = 'procure_admin'
                OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
            )
            WITH CHECK (
                CURRENT_USER = 'procure_admin'
                OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
            );

        -- 7. Grant access to application roles
        GRANT ALL PRIVILEGES ON api_keys, webhook_subscriptions, webhook_deliveries, api_idempotency_records TO procure_admin;
        GRANT SELECT, INSERT, UPDATE, DELETE ON api_keys, webhook_subscriptions, webhook_deliveries, api_idempotency_records TO procure_workflow;
        GRANT SELECT ON api_keys, webhook_subscriptions, webhook_deliveries TO procure_reviewer;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS api_idempotency_records;
        DROP POLICY IF EXISTS webhook_deliveries_tenant_isolation ON webhook_deliveries;
        DROP POLICY IF EXISTS webhook_subscriptions_tenant_isolation ON webhook_subscriptions;
        DROP POLICY IF EXISTS api_keys_tenant_isolation ON api_keys;
        """
    )
