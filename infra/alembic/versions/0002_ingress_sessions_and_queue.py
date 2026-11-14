"""Add workspace_installations, inbound_event_queue, and conversation_sessions.

Revision ID: 0002_ingress_sessions_and_queue
Revises: 0001_initial_tables
Create Date: 2026-11-12 23:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "0002_ingress_sessions_and_queue"
down_revision: str | None = "0001_initial_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create workspace_installations table
    op.create_table(
        "workspace_installations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="tenant_default"),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("external_workspace_id", sa.String(128), nullable=False),
        sa.Column("bot_token", sa.String(255), nullable=False),
        sa.Column("bot_user_id", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "installation_metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "uq_workspace_platform_external_id",
        "workspace_installations",
        ["platform", "external_workspace_id"],
        unique=True,
    )
    op.create_index("ix_workspace_tenant_id", "workspace_installations", ["tenant_id"])

    # 2. Create inbound_event_queue table
    op.create_table(
        "inbound_event_queue",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="tenant_default"),
        sa.Column("ordering_key", sa.String(128), nullable=False, server_default=""),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("normalized_payload", JSONB, nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lock_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_queue_channel_event_id",
        "inbound_event_queue",
        ["channel", "event_id"],
        unique=True,
    )
    op.create_index("ix_queue_tenant_id", "inbound_event_queue", ["tenant_id"])
    op.create_index("ix_queue_ordering_key", "inbound_event_queue", ["ordering_key"])
    # Partial index on pending rows
    op.execute(
        """
        CREATE INDEX ix_queue_pending
        ON inbound_event_queue (available_at, attempts)
        WHERE status = 'pending';
        """
    )

    # 3. Create conversation_sessions table
    op.create_table(
        "conversation_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="tenant_default"),
        sa.Column("channel_type", sa.String(32), nullable=False),
        sa.Column("conversation_id", sa.String(128), nullable=False),
        sa.Column("thread_id", sa.String(128), nullable=False),
        sa.Column(
            "request_id",
            UUID(as_uuid=True),
            sa.ForeignKey("requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("session_data", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_session_channel_thread",
        "conversation_sessions",
        ["tenant_id", "channel_type", "conversation_id", "thread_id"],
        unique=True,
    )
    op.create_index("ix_session_request_id", "conversation_sessions", ["request_id"])
    op.create_index("ix_session_expires_at", "conversation_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_table("conversation_sessions")
    op.execute("DROP INDEX IF EXISTS ix_queue_pending;")
    op.drop_table("inbound_event_queue")
    op.drop_table("workspace_installations")
