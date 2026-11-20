"""Authentication, organizations, sessions, credentials, and invitations.

Revision ID: 0007_auth_organizations_sessions
Revises: 0006_split_db_roles
Create Date: 2026-11-19 02:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_auth_organizations_sessions"
down_revision: str | None = "0006_split_db_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        -- 1. Organizations (tenants)
        CREATE TABLE IF NOT EXISTS organizations (
            id VARCHAR(64) PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        -- 2. Users table
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            email VARCHAR(255) NOT NULL,
            full_name VARCHAR(255) NOT NULL DEFAULT '',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            is_verified BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_users_tenant_email UNIQUE (tenant_id, email)
        );
        CREATE INDEX IF NOT EXISTS ix_users_tenant_id ON users (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_users_email ON users (email);

        -- 3. Credentials (Argon2id password hashes)
        CREATE TABLE IF NOT EXISTS credentials (
            user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            password_hash TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        -- 4. Email verifications
        CREATE TABLE IF NOT EXISTS email_verifications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash VARCHAR(128) NOT NULL UNIQUE,
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_email_verifications_user_id ON email_verifications (user_id);
        CREATE INDEX IF NOT EXISTS ix_email_verifications_token_hash ON email_verifications (token_hash);

        -- 5. Password resets
        CREATE TABLE IF NOT EXISTS password_resets (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash VARCHAR(128) NOT NULL UNIQUE,
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_password_resets_user_id ON password_resets (user_id);
        CREATE INDEX IF NOT EXISTS ix_password_resets_token_hash ON password_resets (token_hash);

        -- 6. Sessions (rotating refresh tokens & CSRF)
        CREATE TABLE IF NOT EXISTS sessions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            tenant_id VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            refresh_token_hash VARCHAR(128) NOT NULL UNIQUE,
            csrf_token VARCHAR(128) NOT NULL,
            is_revoked BOOLEAN NOT NULL DEFAULT FALSE,
            expires_at TIMESTAMPTZ NOT NULL,
            user_agent VARCHAR(512) NULL,
            ip_address VARCHAR(64) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_sessions_user_id ON sessions (user_id);
        CREATE INDEX IF NOT EXISTS ix_sessions_tenant_id ON sessions (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_sessions_refresh_token_hash ON sessions (refresh_token_hash);
        CREATE INDEX IF NOT EXISTS ix_sessions_is_revoked ON sessions (is_revoked);

        -- 7. Invitations
        CREATE TABLE IF NOT EXISTS invitations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            email VARCHAR(255) NOT NULL,
            role VARCHAR(64) NOT NULL,
            token_hash VARCHAR(128) NOT NULL UNIQUE,
            invited_by UUID NULL REFERENCES users(id) ON DELETE SET NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_invitations_tenant_id ON invitations (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_invitations_email ON invitations (email);
        CREATE INDEX IF NOT EXISTS ix_invitations_token_hash ON invitations (token_hash);

        -- 8. TOTP Secrets
        CREATE TABLE IF NOT EXISTS totp_secrets (
            user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            secret VARCHAR(128) NOT NULL,
            is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            backup_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        -- 9. Login Attempts
        CREATE TABLE IF NOT EXISTS login_attempts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email VARCHAR(255) NOT NULL,
            ip_address VARCHAR(64) NOT NULL DEFAULT '',
            success BOOLEAN NOT NULL,
            reason VARCHAR(255) NULL,
            attempted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_login_attempts_email ON login_attempts (email);
        CREATE INDEX IF NOT EXISTS ix_login_attempts_email_attempted ON login_attempts (email, attempted_at);
        CREATE INDEX IF NOT EXISTS ix_login_attempts_ip_attempted ON login_attempts (ip_address, attempted_at);

        -- 10. Ensure user_role_assignments exists
        CREATE TABLE IF NOT EXISTS user_role_assignments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL,
            user_id VARCHAR(128) NOT NULL,
            role_name VARCHAR(64) NOT NULL,
            assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            assigned_by VARCHAR(128) NULL,
            expires_at TIMESTAMPTZ NULL,
            revoked_at TIMESTAMPTZ NULL
        );
        CREATE INDEX IF NOT EXISTS ix_user_role_assignments_tenant_user ON user_role_assignments (tenant_id, user_id);

        -- 11. Seed legal_reviewer role and new permissions
        INSERT INTO rbac_roles (role_name, description)
        VALUES ('legal_reviewer', 'Legal counsel reviewing contracts, MSAs, and regulatory compliance')
        ON CONFLICT (role_name) DO NOTHING;

        INSERT INTO rbac_permissions (id, description)
        VALUES
            ('legal:review', 'Review legal contracts and regulatory compliance'),
            ('contracts:read', 'Read contracts and terms'),
            ('contracts:review', 'Review and approve contracts'),
            ('roles:manage', 'Assign and revoke user roles within tenant'),
            ('users:manage', 'Manage and deprovision users within tenant'),
            ('invitations:create', 'Create and send tenant member invitations'),
            ('invitations:manage', 'Manage and revoke member invitations')
        ON CONFLICT (id) DO NOTHING;

        INSERT INTO rbac_role_permissions (role_name, permission_id)
        VALUES
            ('legal_reviewer', 'legal:review'),
            ('legal_reviewer', 'requests:read'),
            ('legal_reviewer', 'contracts:read'),
            ('legal_reviewer', 'contracts:review'),
            ('tenant_admin', 'roles:manage'),
            ('tenant_admin', 'users:manage'),
            ('tenant_admin', 'invitations:create'),
            ('tenant_admin', 'invitations:manage')
        ON CONFLICT DO NOTHING;

        -- 12. Row Level Security policies (Strict Fail-Closed Dual Wall)
        ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE organizations FORCE ROW LEVEL SECURITY;
        ALTER TABLE users ENABLE ROW LEVEL SECURITY;
        ALTER TABLE users FORCE ROW LEVEL SECURITY;
        ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE sessions FORCE ROW LEVEL SECURITY;
        ALTER TABLE invitations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE invitations FORCE ROW LEVEL SECURITY;
        ALTER TABLE credentials ENABLE ROW LEVEL SECURITY;
        ALTER TABLE credentials FORCE ROW LEVEL SECURITY;
        ALTER TABLE totp_secrets ENABLE ROW LEVEL SECURITY;
        ALTER TABLE totp_secrets FORCE ROW LEVEL SECURITY;

        -- Drop existing policies if any
        DROP POLICY IF EXISTS organizations_tenant_isolation ON organizations;
        DROP POLICY IF EXISTS users_tenant_isolation ON users;
        DROP POLICY IF EXISTS sessions_tenant_isolation ON sessions;
        DROP POLICY IF EXISTS invitations_tenant_isolation ON invitations;

        CREATE POLICY organizations_tenant_isolation ON organizations
            AS PERMISSIVE FOR ALL
            USING (
                CURRENT_USER = 'procure_admin'
                OR id = NULLIF(current_setting('app.tenant_id', true), '')
            )
            WITH CHECK (
                CURRENT_USER = 'procure_admin'
                OR id = NULLIF(current_setting('app.tenant_id', true), '')
            );

        CREATE POLICY users_tenant_isolation ON users
            AS PERMISSIVE FOR ALL
            USING (
                CURRENT_USER = 'procure_admin'
                OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
            )
            WITH CHECK (
                CURRENT_USER = 'procure_admin'
                OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
            );

        CREATE POLICY sessions_tenant_isolation ON sessions
            AS PERMISSIVE FOR ALL
            USING (
                CURRENT_USER = 'procure_admin'
                OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
            )
            WITH CHECK (
                CURRENT_USER = 'procure_admin'
                OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
            );

        CREATE POLICY invitations_tenant_isolation ON invitations
            AS PERMISSIVE FOR ALL
            USING (
                CURRENT_USER = 'procure_admin'
                OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
            )
            WITH CHECK (
                CURRENT_USER = 'procure_admin'
                OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
            );

        -- Grants to app roles
        GRANT ALL PRIVILEGES ON organizations, users, credentials, email_verifications,
            password_resets, sessions, invitations, totp_secrets, login_attempts,
            user_role_assignments TO procure_admin;

        GRANT SELECT, INSERT, UPDATE, DELETE ON organizations, users, credentials,
            email_verifications, password_resets, sessions, invitations,
            totp_secrets, login_attempts, user_role_assignments TO procure_workflow;

        GRANT SELECT ON organizations, users, sessions, invitations,
            user_role_assignments TO procure_reviewer;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS login_attempts CASCADE;
        DROP TABLE IF EXISTS totp_secrets CASCADE;
        DROP TABLE IF EXISTS invitations CASCADE;
        DROP TABLE IF EXISTS sessions CASCADE;
        DROP TABLE IF EXISTS password_resets CASCADE;
        DROP TABLE IF EXISTS email_verifications CASCADE;
        DROP TABLE IF EXISTS credentials CASCADE;
        DROP TABLE IF EXISTS users CASCADE;
        DROP TABLE IF EXISTS organizations CASCADE;
        """
    )
