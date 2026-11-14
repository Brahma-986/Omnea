"""Add procurement_intents table.

Revision ID: 0003_procurement_intents
Revises: 0002_ingress_sessions_and_queue
Create Date: 2026-11-12 23:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "0003_procurement_intents"
down_revision: str | None = "0002_ingress_sessions_and_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "procurement_intents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="tenant_default"),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conversation_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "request_id",
            UUID(as_uuid=True),
            sa.ForeignKey("requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_event_id", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False, server_default="v1"),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("intent_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="awaiting_clarification"),
        sa.Column("clarification_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        # Extracted fields
        sa.Column("vendor_name", sa.String(255), nullable=True),
        sa.Column("replaces_vendor", sa.String(255), nullable=True),
        sa.Column(
            "candidate_vendors", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("estimated_spend", sa.Numeric(18, 4), nullable=True),
        sa.Column("annualized_spend", sa.Numeric(18, 4), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("billing_period", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.Column("department", sa.String(128), nullable=True),
        sa.Column("tags", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        # Verification & evidence
        sa.Column("evidence", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("missing_fields", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("clarifying_question", sa.Text(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "currency IS NULL OR length(currency) = 3",
            name="ck_procurement_intents_currency",
        ),
    )

    op.create_index("ix_intents_tenant_id", "procurement_intents", ["tenant_id"])
    op.create_index("ix_intents_session_id", "procurement_intents", ["session_id"])
    op.create_index("ix_intents_request_id", "procurement_intents", ["request_id"])
    op.create_index("ix_intents_source_event", "procurement_intents", ["source_event_id"])
    op.create_index("ix_intents_vendor_name", "procurement_intents", ["vendor_name"])
    op.create_index("ix_intents_status", "procurement_intents", ["tenant_id", "status"])


def downgrade() -> None:
    op.drop_table("procurement_intents")
