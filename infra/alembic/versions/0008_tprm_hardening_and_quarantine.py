"""TPRM hardening, quarantine, risk register, and control findings.

Revision ID: 0008_tprm_hardening_and_quarantine
Revises: 0007_auth_organizations_sessions
Create Date: 2026-11-19 22:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_tprm_hardening"
down_revision: str | None = "0007_auth_organizations_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        -- 1. Update check constraint on vendor_security_reviews to support quarantine, qualified opinion, and supersession
        ALTER TABLE vendor_security_reviews
        DROP CONSTRAINT IF EXISTS chk_security_review_status;

        ALTER TABLE vendor_security_reviews
        ADD CONSTRAINT chk_security_review_status
        CHECK (status IN ('compliant', 'pending_review', 'expired', 'remediation_required', 'quarantined', 'qualified_opinion', 'superseded'));

        -- 2. TPRM Risk Register per vendor
        CREATE TABLE IF NOT EXISTS tprm_risk_register (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL,
            vendor_id UUID NOT NULL,
            risk_tier VARCHAR(32) NOT NULL DEFAULT 'medium',
            overall_status VARCHAR(32) NOT NULL DEFAULT 'pending_review',
            findings_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            active_risks JSONB NOT NULL DEFAULT '[]'::jsonb,
            next_review_date DATE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_tprm_risk_register_vendor UNIQUE (tenant_id, vendor_id),
            CONSTRAINT fk_tprm_risk_register_vendor FOREIGN KEY (tenant_id, vendor_id)
                REFERENCES vendors (tenant_id, id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_tprm_risk_register_tenant ON tprm_risk_register (tenant_id, risk_tier);

        -- 3. Structured Control Findings per review document
        CREATE TABLE IF NOT EXISTS tprm_control_findings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL,
            vendor_id UUID NOT NULL,
            security_review_id UUID NOT NULL REFERENCES vendor_security_reviews (id) ON DELETE CASCADE,
            domain VARCHAR(64) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'not_found',
            page_number INT,
            quote TEXT,
            start_offset INT,
            end_offset INT,
            verified BOOLEAN NOT NULL DEFAULT FALSE,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_tprm_findings_vendor FOREIGN KEY (tenant_id, vendor_id)
                REFERENCES vendors (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT chk_tprm_finding_status CHECK (status IN ('satisfied', 'qualified', 'remediation_required', 'not_found'))
        );
        CREATE INDEX IF NOT EXISTS idx_tprm_findings_review ON tprm_control_findings (tenant_id, security_review_id);
        CREATE INDEX IF NOT EXISTS idx_tprm_findings_vendor ON tprm_control_findings (tenant_id, vendor_id);

        -- 4. Grants on new tables if application roles exist
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'procure_reviewer') THEN
                GRANT SELECT ON tprm_risk_register, tprm_control_findings TO procure_reviewer;
            END IF;
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'procure_workflow') THEN
                GRANT SELECT ON tprm_risk_register, tprm_control_findings TO procure_workflow;
            END IF;
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'procure_ingest') THEN
                GRANT SELECT, INSERT, UPDATE ON tprm_risk_register, tprm_control_findings TO procure_ingest;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS tprm_control_findings;
        DROP TABLE IF EXISTS tprm_risk_register;
        ALTER TABLE vendor_security_reviews DROP CONSTRAINT IF EXISTS chk_security_review_status;
        ALTER TABLE vendor_security_reviews ADD CONSTRAINT chk_security_review_status
            CHECK (status IN ('compliant', 'pending_review', 'expired', 'remediation_required'));
        """
    )
