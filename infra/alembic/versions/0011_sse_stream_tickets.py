"""Stage 9 follow-up: SSE Stream Tickets table for cross-worker storage and atomic consumption.

Revision ID: 0011_sse_stream_tickets
Revises: 0010_stage7_developer_platform
Create Date: 2026-11-20 20:35:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_sse_stream_tickets"
down_revision: str | None = "0010_stage7_developer_platform"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sse_stream_tickets (
            ticket VARCHAR(128) PRIMARY KEY,
            user_id VARCHAR(128) NOT NULL,
            tenant_id VARCHAR(64) NOT NULL,
            roles JSONB NOT NULL DEFAULT '[]'::jsonb,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS ix_sse_stream_tickets_expires_at ON sse_stream_tickets (expires_at);
        CREATE INDEX IF NOT EXISTS ix_sse_stream_tickets_tenant_id ON sse_stream_tickets (tenant_id);

        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'procure_admin') THEN
                GRANT ALL PRIVILEGES ON sse_stream_tickets TO procure_admin;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'procure_workflow') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON sse_stream_tickets TO procure_workflow;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'procure_reviewer') THEN
                GRANT SELECT ON sse_stream_tickets TO procure_reviewer;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sse_stream_tickets;")
