"""Cryptographically verifiable append-only audit hash chain manager."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class AuditChainVerificationResult:
    """Verification summary for a tenant's audit hash chain."""

    is_valid: bool
    total_entries: int
    tampered_sequence: int | None = None


class AuditHashChainManager:
    """Manages appending and verifying cryptographic SHA-256 hash chains for audit records."""

    CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS audit_hash_chain (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id VARCHAR(64) NOT NULL,
        sequence_number BIGSERIAL,
        record_type VARCHAR(64) NOT NULL,
        record_id VARCHAR(128) NOT NULL,
        actor_id VARCHAR(128) NOT NULL,
        action VARCHAR(64) NOT NULL,
        payload JSONB NOT NULL DEFAULT '{}',
        prev_hash VARCHAR(64) NOT NULL,
        entry_hash VARCHAR(64) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    );
    """

    @classmethod
    async def _ensure_table(cls, session: AsyncSession) -> None:
        """Ensure the audit_hash_chain table, constraints, and RLS policies exist."""
        await session.execute(text(cls.CREATE_TABLE_SQL))
        await session.execute(
            text(
                "ALTER TABLE audit_hash_chain "
                "ADD COLUMN IF NOT EXISTS payload JSONB NOT NULL DEFAULT '{}';"
            )
        )
        await session.execute(
            text(
                "DO $$ BEGIN "
                "IF EXISTS ("
                "    SELECT 1 FROM information_schema.columns "
                "    WHERE table_name = 'audit_hash_chain' AND column_name = 'payload_hash'"
                ") THEN "
                "    ALTER TABLE audit_hash_chain ALTER COLUMN payload_hash DROP NOT NULL; "
                "END IF; "
                "END $$;"
            )
        )
        try:
            await session.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_audit_hash_chain_tenant_seq "
                    "ON audit_hash_chain (tenant_id, sequence_number);"
                )
            )
        except Exception:
            pass

        try:
            await session.execute(text("ALTER TABLE audit_hash_chain ENABLE ROW LEVEL SECURITY;"))
            await session.execute(text("ALTER TABLE audit_hash_chain FORCE ROW LEVEL SECURITY;"))
            await session.execute(
                text(
                    "DO $$ BEGIN "
                    "IF NOT EXISTS ("
                    "    SELECT 1 FROM pg_policies "
                    "    WHERE tablename = 'audit_hash_chain' "
                    "      AND policyname = 'audit_hash_chain_tenant_isolation'"
                    ") THEN "
                    "    CREATE POLICY audit_hash_chain_tenant_isolation ON audit_hash_chain "
                    "    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')) "
                    "    WITH CHECK ("
                    "        tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
                    "    ); "
                    "END IF; "
                    "END $$;"
                )
            )
        except Exception:
            pass

    @classmethod
    async def append_entry(
        cls,
        session: AsyncSession,
        tenant_id: str,
        record_type: str,
        record_id: str,
        actor_id: str,
        action: str,
        payload: dict[str, Any],
    ) -> str:
        """Append an immutable hashed audit record linked to the previous tenant entry.

        Args:
            session: Active database session.
            tenant_id: Tenant identifier.
            record_type: Type of the entity being audited.
            record_id: Identifier of the audited entity.
            actor_id: Identity of the actor performing the mutation.
            action: Specific action performed.
            payload: Relevant state changes or event payload.

        Returns:
            str: The computed SHA-256 entry hash.
        """
        # 1. Ensure table exists
        await cls._ensure_table(session)

        # 2. Acquire transaction-level advisory lock on tenant to prevent concurrent chain forks
        try:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:t))"),
                {"t": tenant_id},
            )
        except Exception:
            pass

        # 3. Fetch latest entry for tenant_id
        latest_stmt = text(
            """
            SELECT sequence_number, entry_hash
            FROM audit_hash_chain
            WHERE tenant_id = :tenant_id
            ORDER BY sequence_number DESC
            LIMIT 1;
            """
        )
        res = await session.execute(latest_stmt, {"tenant_id": tenant_id})
        latest_row = res.mappings().first()

        if latest_row is None:
            prev_hash = GENESIS_HASH
            sequence = 1
        else:
            prev_hash = str(latest_row["entry_hash"])
            sequence = int(latest_row["sequence_number"]) + 1

        # 3. Compute entry hash
        sorted_payload_json = json.dumps(payload, sort_keys=True)
        hash_payload = (
            f"{tenant_id}:{sequence}:{record_type}:{record_id}:{actor_id}:"
            f"{action}:{sorted_payload_json}:{prev_hash}"
        )
        entry_hash = hashlib.sha256(hash_payload.encode("utf-8")).hexdigest()

        # 4. Insert row
        insert_stmt = text(
            """
            INSERT INTO audit_hash_chain (
                tenant_id,
                sequence_number,
                record_type,
                record_id,
                actor_id,
                action,
                payload,
                prev_hash,
                entry_hash
            ) VALUES (
                :tenant_id,
                :sequence_number,
                :record_type,
                :record_id,
                :actor_id,
                :action,
                CAST(:payload AS jsonb),
                :prev_hash,
                :entry_hash
            );
            """
        )
        await session.execute(
            insert_stmt,
            {
                "tenant_id": tenant_id,
                "sequence_number": sequence,
                "record_type": record_type,
                "record_id": record_id,
                "actor_id": actor_id,
                "action": action,
                "payload": sorted_payload_json,
                "prev_hash": prev_hash,
                "entry_hash": entry_hash,
            },
        )
        await session.flush()
        return entry_hash

    @classmethod
    async def verify_chain(
        cls,
        session: AsyncSession,
        tenant_id: str,
    ) -> AuditChainVerificationResult:
        """Verify the cryptographic integrity of a tenant's audit hash chain.

        Args:
            session: Active database session.
            tenant_id: Scope tenant identifier to verify.

        Returns:
            AuditChainVerificationResult with validation status and tampered sequence if any.
        """
        await cls._ensure_table(session)

        query = text(
            """
            SELECT sequence_number, record_type, record_id, actor_id, action,
                   payload, prev_hash, entry_hash
            FROM audit_hash_chain
            WHERE tenant_id = :tenant_id
            ORDER BY sequence_number ASC;
            """
        )
        rows = (await session.execute(query, {"tenant_id": tenant_id})).mappings().all()

        if not rows:
            return AuditChainVerificationResult(
                is_valid=True,
                total_entries=0,
                tampered_sequence=None,
            )

        expected_prev_hash = GENESIS_HASH

        for row in rows:
            seq = int(row["sequence_number"])
            rec_type = str(row["record_type"])
            rec_id = str(row["record_id"])
            actor = str(row["actor_id"])
            act = str(row["action"])
            raw_payload = row["payload"]
            if isinstance(raw_payload, str):
                try:
                    payload_obj = json.loads(raw_payload)
                except Exception:
                    payload_obj = raw_payload
            else:
                payload_obj = raw_payload

            sorted_payload_json = json.dumps(payload_obj, sort_keys=True)
            prev_h = str(row["prev_hash"])
            entry_h = str(row["entry_hash"])

            if prev_h != expected_prev_hash:
                return AuditChainVerificationResult(
                    is_valid=False,
                    total_entries=len(rows),
                    tampered_sequence=seq,
                )

            hash_payload = (
                f"{tenant_id}:{seq}:{rec_type}:{rec_id}:{actor}:"
                f"{act}:{sorted_payload_json}:{prev_h}"
            )
            computed_hash = hashlib.sha256(hash_payload.encode("utf-8")).hexdigest()

            if entry_h != computed_hash:
                return AuditChainVerificationResult(
                    is_valid=False,
                    total_entries=len(rows),
                    tampered_sequence=seq,
                )

            expected_prev_hash = entry_h

        return AuditChainVerificationResult(
            is_valid=True,
            total_entries=len(rows),
            tampered_sequence=None,
        )
