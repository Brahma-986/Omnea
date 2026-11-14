"""Base ingestion connector and result schemas for batch data feeds."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from procure_context.resolution.normalizer import normalize_vendor_name
from procure_context.resolution.resolver import EntityResolutionService
from procure_core.models.vendor import SpendTransactionModel, VendorModel

logger = logging.getLogger(__name__)


@dataclass
class IngestionRow:
    """Standardized representation of a single ingestion spend row."""

    source_id: str
    vendor_name: str
    amount: Decimal
    currency: str
    transaction_date: date
    category: str = "general"
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestionResult:
    """Aggregate summary of batch ingestion execution."""

    total_rows: int = 0
    processed_rows: int = 0
    matched_count: int = 0
    subsidiary_count: int = 0
    review_count: int = 0
    novel_count: int = 0
    total_spend_by_currency: dict[str, Decimal] = field(default_factory=dict)
    dry_run: bool = False
    records: list[dict[str, Any]] = field(default_factory=list)


class BaseConnector:
    """Abstract base class for ingestion connectors (CSV, ERP feeds)."""

    def __init__(
        self,
        tenant_id: str,
        source_system: str,
        resolver: EntityResolutionService | None = None,
    ) -> None:
        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id must be non-empty")
        self.tenant_id = tenant_id
        self.source_system = source_system
        self.resolver = resolver or EntityResolutionService(tenant_id)

    async def process_row(
        self,
        session: AsyncSession,
        row: IngestionRow,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Resolve entity, handle idempotency, and persist spend transaction if not dry_run."""
        # 1. Run through Entity Resolution Service
        res = await self.resolver.resolve_transactional(
            session, row.vendor_name, commit_if_auto=not dry_run
        )

        target_vendor_id = res.matched_vendor_id

        # 2. If new entity and not dry-run, create new vendor record
        if res.outcome == "NEW_ENTITY" and not dry_run:
            norm = normalize_vendor_name(row.vendor_name)
            new_vendor = VendorModel(
                tenant_id=self.tenant_id,
                canonical_key=norm.canonical_key,
                name=row.vendor_name,
                normalized_base=norm.normalized_base,
                region=norm.region,
                status="active",
                metadata_={
                    "source_system": self.source_system,
                    "source_id": row.source_id,
                },
            )
            session.add(new_vendor)
            await session.flush()
            target_vendor_id = new_vendor.id

        # 3. If not dry run and we have a target vendor, upsert spend transaction idempotently
        if not dry_run and target_vendor_id:
            prov = dict(row.provenance)
            prov["source_system"] = self.source_system
            prov["source_id"] = row.source_id
            prov["raw_vendor_name"] = row.vendor_name

            stmt = (
                pg_insert(SpendTransactionModel)
                .values(
                    tenant_id=self.tenant_id,
                    vendor_id=target_vendor_id,
                    amount=row.amount,
                    currency=row.currency.upper(),
                    category=row.category,
                    transaction_date=row.transaction_date,
                    source_system=self.source_system,
                    source_id=row.source_id,
                    provenance=prov,
                )
                .on_conflict_do_update(
                    constraint="uq_spend_tenant_source",
                    set_={
                        "last_seen": func.now(),
                        "amount": row.amount,
                        "vendor_id": target_vendor_id,
                        "provenance": prov,
                    },
                )
            )
            await session.execute(stmt)

        return {
            "source_id": row.source_id,
            "vendor_name": row.vendor_name,
            "resolved_vendor_id": target_vendor_id,
            "outcome": res.outcome,
            "confidence": res.confidence,
            "amount": row.amount,
            "currency": row.currency.upper(),
            "needs_review": res.needs_review,
        }
