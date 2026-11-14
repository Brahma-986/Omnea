"""Enterprise Context Engine: vendors, embeddings, decisions, spend, and review tasks.

Revision ID: 0004_enterprise_context
Revises: 0003_procurement_intents
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_enterprise_context"
down_revision: str | None = "0003_procurement_intents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Candidate blocking extension
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

    # 2. Add UNIQUE(tenant_id, id) constraint to procurement_intents
    # Note: tenant_id is NOT NULL and needs_review already exists from 0003
    op.create_unique_constraint(
        "uq_procurement_intents_tenant_id",
        "procurement_intents",
        ["tenant_id", "id"],
    )

    # 2b. Add UNIQUE(tenant_id, id) to requests, and vendor_id to requests & approvals
    op.create_unique_constraint(
        "uq_requests_tenant_id",
        "requests",
        ["tenant_id", "id"],
    )
    op.add_column(
        "requests",
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "approvals",
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # 3. Tenant Resolution Settings (Versioned thresholds per tenant)
    op.create_table(
        "tenant_resolution_settings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column(
            "high_priority_threshold",
            sa.Numeric(4, 3),
            nullable=False,
            server_default="0.880",
        ),
        sa.Column("review_threshold", sa.Numeric(4, 3), nullable=False, server_default="0.650"),
        sa.Column("weight_jw", sa.Numeric(4, 3), nullable=False, server_default="0.500"),
        sa.Column("weight_emb", sa.Numeric(4, 3), nullable=False, server_default="0.500"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "review_threshold < high_priority_threshold",
            name="chk_review_lt_high_priority",
        ),
        sa.CheckConstraint("weight_jw + weight_emb = 1.000", name="chk_weights_sum_one"),
        sa.UniqueConstraint("tenant_id", "version", name="uq_tenant_settings_version"),
    )
    op.execute("""
        CREATE UNIQUE INDEX uq_active_tenant_settings
        ON tenant_resolution_settings (tenant_id)
        WHERE is_active = TRUE;
    """)

    # 4. Vendors (Identity Ledger & Hierarchy owned by Postgres)
    op.create_table(
        "vendors",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("canonical_key", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("normalized_base", sa.String(255), nullable=False),
        sa.Column("region", sa.String(64), nullable=True),
        sa.Column("legal_name", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("previous_status", sa.String(32), nullable=True),
        sa.Column("parent_vendor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("merged_into_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'pending_review', 'merged')",
            name="chk_vendor_status",
        ),
        sa.CheckConstraint(
            "(status = 'merged' AND merged_into_id IS NOT NULL) "
            "OR (status <> 'merged' AND merged_into_id IS NULL)",
            name="chk_merged_consistency",
        ),
        sa.CheckConstraint(
            "(status = 'merged') = (previous_status IS NOT NULL)",
            name="chk_merged_previous_status",
        ),
        sa.CheckConstraint(
            "merged_into_id IS NULL OR merged_into_id <> id",
            name="chk_no_self_merge",
        ),
        sa.CheckConstraint(
            "parent_vendor_id IS NULL OR parent_vendor_id <> id",
            name="chk_no_self_parent",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_vendors_tenant_id"),
        sa.UniqueConstraint("tenant_id", "canonical_key", name="uq_vendors_tenant_canonical_key"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_vendors_parent",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "merged_into_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_vendors_merged_into",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("idx_vendors_tenant_normalized", "vendors", ["tenant_id", "normalized_base"])
    op.execute("CREATE INDEX idx_vendors_trgm ON vendors USING gin (normalized_base gin_trgm_ops);")

    # 4b. Hierarchy Validation Trigger (depth <= 4 and cycle prevention in DB)
    op.execute("""
        CREATE OR REPLACE FUNCTION check_vendor_hierarchy()
        RETURNS TRIGGER AS $$
        DECLARE
            v_anc_depth INT;
            v_sub_depth INT;
            v_cycle BOOLEAN;
        BEGIN
            -- Only fire when parent_vendor_id changes
            IF TG_OP = 'UPDATE'
               AND OLD.parent_vendor_id IS NOT DISTINCT FROM NEW.parent_vendor_id THEN
                RETURN NEW;
            END IF;

            IF NEW.parent_vendor_id IS NULL THEN
                RETURN NEW;
            END IF;

            -- 1. Acquire per-tenant hierarchy advisory lock
            PERFORM pg_advisory_xact_lock(
                hashtextextended(NEW.tenant_id || chr(58) || 'hierarchy', 0)
            );

            -- 2. Reject self-parent
            IF NEW.parent_vendor_id = NEW.id THEN
                RAISE EXCEPTION 'Vendor cannot be its own parent (id=%)', NEW.id;
            END IF;

            -- 2b. Reject setting parent to a merged vendor
            IF EXISTS (
                SELECT 1 FROM vendors
                WHERE tenant_id = NEW.tenant_id AND id = NEW.parent_vendor_id AND status = 'merged'
            ) THEN
                RAISE EXCEPTION 'Cannot set parent to a merged vendor (id=%)', NEW.parent_vendor_id;
            END IF;

            -- 3. Calculate ancestor depth above target parent and detect cycles
            WITH RECURSIVE ancestor_tree AS (
                SELECT id, parent_vendor_id, 1 AS depth
                FROM vendors
                WHERE tenant_id = NEW.tenant_id AND id = NEW.parent_vendor_id
                UNION ALL
                SELECT v.id, v.parent_vendor_id, at.depth + 1
                FROM vendors v
                JOIN ancestor_tree at ON v.id = at.parent_vendor_id
                WHERE v.tenant_id = NEW.tenant_id AND at.depth < 10
            )
            SELECT COALESCE(MAX(depth), 0), COALESCE(bool_or(id = NEW.id), false)
            INTO v_anc_depth, v_cycle
            FROM ancestor_tree;

            IF v_cycle THEN
                RAISE EXCEPTION
                    'Hierarchy cycle detected: vendor % is an ancestor of %',
                    NEW.id, NEW.parent_vendor_id;
            END IF;

            -- 4. Calculate subtree depth below NEW.id
            WITH RECURSIVE descendant_tree AS (
                SELECT id, 0 AS depth
                FROM vendors
                WHERE tenant_id = NEW.tenant_id AND id = NEW.id
                UNION ALL
                SELECT v.id, dt.depth + 1
                FROM vendors v
                JOIN descendant_tree dt ON v.parent_vendor_id = dt.id
                WHERE v.tenant_id = NEW.tenant_id AND dt.depth < 10
            )
            SELECT COALESCE(MAX(depth), 0)
            INTO v_sub_depth
            FROM descendant_tree;

            IF (v_anc_depth + 1 + v_sub_depth) > 4 THEN
                RAISE EXCEPTION
                    'Hierarchy depth limit exceeded: total depth % > 4',
                    (v_anc_depth + 1 + v_sub_depth);
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_check_vendor_hierarchy
        BEFORE INSERT OR UPDATE OF parent_vendor_id ON vendors
        FOR EACH ROW
        EXECUTE FUNCTION check_vendor_hierarchy();
    """)

    # 4c. Link requests and approvals to vendors via composite FK
    op.create_foreign_key(
        "fk_requests_vendor",
        "requests",
        "vendors",
        ["tenant_id", "vendor_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_approvals_vendor",
        "approvals",
        "vendors",
        ["tenant_id", "vendor_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )

    # 5. Vendor Aliases (RESTRICT on delete)
    op.create_table(
        "vendor_aliases",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alias", sa.String(255), nullable=False),
        sa.Column("normalized_alias", sa.String(255), nullable=False),
        sa.Column("source", sa.String(64), nullable=False, server_default="manual"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("tenant_id", "normalized_alias", name="uq_vendor_aliases_tenant_alias"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_vendor_aliases_vendor",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("idx_vendor_aliases_vendor", "vendor_aliases", ["tenant_id", "vendor_id"])

    # 6. Vendor Embeddings (Raw vector(384) SQL, dimension=384 CHECK, RESTRICT on delete)
    op.execute("""
        CREATE TABLE vendor_embeddings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL,
            vendor_id UUID NOT NULL,
            embedding_type VARCHAR(32) NOT NULL DEFAULT 'canonical_name',
            model VARCHAR(128) NOT NULL,
            dimension INTEGER NOT NULL,
            embedding vector(384) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT chk_embedding_dim_384 CHECK (dimension = 384),
            CONSTRAINT uq_vendor_embedding_unique
                UNIQUE (tenant_id, vendor_id, embedding_type, model),
            CONSTRAINT fk_vendor_embeddings_vendor FOREIGN KEY (tenant_id, vendor_id)
                REFERENCES vendors(tenant_id, id) ON DELETE RESTRICT
        );
        CREATE INDEX idx_vendor_embeddings_lookup ON vendor_embeddings (tenant_id, vendor_id);
    """)

    # 7. Spend Transactions (Spend Source of Truth, RESTRICT on delete)
    op.create_table(
        "spend_transactions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("category", sa.String(128), nullable=False, server_default="general"),
        sa.Column("source_system", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id", "source_system", "source_id", name="uq_spend_tenant_source"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_spend_vendor",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "idx_spend_tenant_vendor_date",
        "spend_transactions",
        ["tenant_id", "vendor_id", "transaction_date"],
    )

    # 8. Contracts (PostgreSQL authoritative source of record)
    op.create_table(
        "contracts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("total_value", sa.Numeric(18, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'expired', 'terminated', 'superseded')",
            name="chk_contract_status",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_contracts_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_contracts_vendor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "supersedes_id"],
            ["contracts.tenant_id", "contracts.id"],
            name="fk_contracts_supersedes",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("idx_contracts_tenant_vendor", "contracts", ["tenant_id", "vendor_id"])

    # 9. Vendor Security Reviews (PostgreSQL authoritative source of record)
    op.create_table(
        "vendor_security_reviews",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("doc_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="compliant"),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("expiration_date", sa.Date(), nullable=False),
        sa.Column("s3_key", sa.String(512), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('compliant', 'pending_review', 'expired', 'remediation_required')",
            name="chk_security_review_status",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_security_reviews_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_security_reviews_vendor",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "idx_security_reviews_tenant_vendor",
        "vendor_security_reviews",
        ["tenant_id", "vendor_id"],
    )

    # 10. Resolution Decisions (Audit Log & Decision History)
    op.create_table(
        "resolution_decisions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("settings_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resolver_version", sa.String(32), nullable=False),
        sa.Column("threshold_set_version", sa.String(32), nullable=False),
        sa.Column("rule_path", sa.String(64), nullable=False),
        sa.Column("input_name", sa.String(255), nullable=False),
        sa.Column("normalized_form", sa.String(255), nullable=False),
        sa.Column("canonical_key", sa.String(255), nullable=False),
        sa.Column("matched_vendor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("composite_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("degraded_reason", sa.String(64), nullable=True),
        sa.Column("needs_rescoring", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "component_scores",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "candidates",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "outcome IN ('match', 'subsidiary_of', 'provisional_review', 'new_entity')",
            name="chk_decision_outcome",
        ),
        sa.CheckConstraint(
            "rule_path IN ("
            "'alias', 'canonical_key', 'exact_parent_region', 'loose_key', "
            "'strong_identifier', 'fuzzy_review', 'embedding_review', 'low_confidence_new')",
            name="chk_decision_rule_path",
        ),
        sa.ForeignKeyConstraint(
            ["settings_id"],
            ["tenant_resolution_settings.id"],
            name="fk_decision_settings",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "matched_vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_decision_vendor",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "idx_resolution_decisions_tenant", "resolution_decisions", ["tenant_id", "created_at"]
    )

    # 11. Intent Vendor Resolutions (Phase 2 Linkage, RESTRICT on delete)
    op.create_table(
        "intent_vendor_resolutions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "outcome IN ('match', 'subsidiary_of', 'provisional_review', 'new_entity')",
            name="chk_ivr_outcome",
        ),
        sa.CheckConstraint(
            "role IN ('vendor_name', 'replaces_vendor', 'candidate_vendor')",
            name="chk_ivr_role",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "intent_id",
            "role",
            "source_name",
            name="uq_intent_vendor_resolutions",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "intent_id"],
            ["procurement_intents.tenant_id", "procurement_intents.id"],
            name="fk_ivr_intent",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["resolution_decisions.id"],
            name="fk_ivr_decision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_ivr_vendor",
            ondelete="RESTRICT",
        ),
    )

    # 12. Entity Review Tasks (Human Review Queue, RESTRICT on delete)
    op.create_table(
        "entity_review_tasks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column(
            "task_type",
            sa.String(32),
            nullable=False,
            server_default="duplicate_review",
        ),
        sa.Column("provisional_vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_vendor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column(
            "component_scores",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("assignee", sa.String(128), nullable=True),
        sa.Column("slack_message_ts", sa.String(64), nullable=True),
        sa.Column("resolved_by", sa.String(128), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "task_type IN ('duplicate_review', 'suggested_parent', 'subsidiary_review')",
            name="chk_review_task_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved_merge', 'approved_subsidiary', "
            "'approved_new', 'rejected', 'superseded_by_merge')",
            name="chk_review_task_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "provisional_vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_ert_provisional",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_ert_candidate",
            ondelete="RESTRICT",
        ),
    )
    op.execute("""
        CREATE UNIQUE INDEX uq_pending_review_pair
        ON entity_review_tasks (
            tenant_id,
            task_type,
            provisional_vendor_id,
            COALESCE(candidate_vendor_id, '00000000-0000-0000-0000-000000000000'::uuid)
        )
        WHERE status = 'pending';
    """)

    # 13. Neo4j Outbox Table (Outbox pattern with ordering, idempotency, and SKIP LOCKED index)
    op.create_table(
        "neo4j_outbox",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "sequence",
            sa.BigInteger(),
            sa.Identity(start=1, cycle=False),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(128), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("ordering_key", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'processed', 'dead_letter')",
            name="chk_outbox_status",
        ),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_outbox_idempotency"),
    )
    op.execute("""
        CREATE INDEX idx_neo4j_outbox_pending
        ON neo4j_outbox (ordering_key, sequence)
        WHERE status = 'pending';
    """)


def downgrade() -> None:
    op.drop_table("neo4j_outbox")
    op.drop_table("entity_review_tasks")
    op.drop_table("intent_vendor_resolutions")
    op.drop_table("resolution_decisions")
    op.drop_table("vendor_security_reviews")
    op.drop_table("contracts")
    op.drop_table("spend_transactions")
    op.execute("DROP TABLE IF EXISTS vendor_embeddings;")
    op.drop_table("vendor_aliases")
    op.execute("ALTER TABLE approvals DROP CONSTRAINT IF EXISTS fk_approvals_vendor;")
    op.execute("ALTER TABLE approvals DROP COLUMN IF EXISTS vendor_id;")
    op.execute("ALTER TABLE requests DROP CONSTRAINT IF EXISTS fk_requests_vendor;")
    op.execute("ALTER TABLE requests DROP COLUMN IF EXISTS vendor_id;")
    op.execute("ALTER TABLE requests DROP CONSTRAINT IF EXISTS uq_requests_tenant_id;")
    op.execute("DROP TRIGGER IF EXISTS trg_check_vendor_hierarchy ON vendors;")
    op.execute("DROP FUNCTION IF EXISTS check_vendor_hierarchy();")
    op.drop_table("vendors")
    op.drop_table("tenant_resolution_settings")
    op.drop_constraint("uq_procurement_intents_tenant_id", "procurement_intents", type_="unique")
