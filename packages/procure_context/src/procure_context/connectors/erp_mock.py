"""Simulated ERP connector for vendor and spend synchronization."""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from procure_context.connectors.base import BaseConnector, IngestionResult, IngestionRow
from procure_context.resolution.resolver import EntityResolutionService

logger = logging.getLogger(__name__)

DEFAULT_ERP_MAPPING = {
    "source_id": "document_id",
    "vendor_name": "supplier_name",
    "amount": "total_amount",
    "currency": "currency",
    "transaction_date": "posting_date",
    "category": "cost_center",
}


def _parse_erp_date(val: Any) -> date:
    if isinstance(val, date):
        return val
    if isinstance(val, str) and val.strip():
        for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(val.strip(), fmt).date()
            except ValueError:
                continue
    return date.today()


class ErpMockConnector(BaseConnector):
    """Simulates an ERP invoice feed (e.g. SAP, NetSuite, Workday) with JSON records."""

    def __init__(
        self,
        tenant_id: str,
        source_system: str = "sap_erp_mock",
        column_mapping: dict[str, str] | None = None,
        resolver: EntityResolutionService | None = None,
    ) -> None:
        super().__init__(tenant_id, source_system, resolver)
        self.column_mapping = column_mapping or DEFAULT_ERP_MAPPING

    async def ingest_payload(
        self,
        session: AsyncSession,
        records: list[dict[str, Any]],
        dry_run: bool = False,
    ) -> IngestionResult:
        """Ingest ERP structured payload through entity resolution and idempotent spend ledger."""
        res = IngestionResult(dry_run=dry_run, total_rows=len(records))
        m = self.column_mapping

        for idx, rec in enumerate(records):
            v_name = str(rec.get(m.get("vendor_name", "supplier_name"), "")).strip()
            if not v_name:
                continue

            amt_raw = rec.get(m.get("amount", "total_amount"), 0)
            amount = Decimal(str(amt_raw))
            currency = str(rec.get(m.get("currency", "currency"), "USD")).strip().upper()
            src_id = str(rec.get(m.get("source_id", "document_id"), f"erp-{idx + 1}")).strip()
            tx_date = _parse_erp_date(rec.get(m.get("transaction_date", "posting_date")))
            category = str(rec.get(m.get("category", "cost_center"), "general")).strip()

            ing_row = IngestionRow(
                source_id=src_id,
                vendor_name=v_name,
                amount=amount,
                currency=currency,
                transaction_date=tx_date,
                category=category,
                provenance={"erp_source": self.source_system, "raw_record": rec},
            )

            record = await self.process_row(session, ing_row, dry_run=dry_run)
            res.records.append(record)
            res.processed_rows += 1

            outcome = record["outcome"]
            if outcome == "MATCH":
                res.matched_count += 1
            elif outcome == "SUBSIDIARY_OF":
                res.subsidiary_count += 1
            elif outcome == "PROVISIONAL_REVIEW":
                res.review_count += 1
            else:
                res.novel_count += 1

            res.total_spend_by_currency[currency] = (
                res.total_spend_by_currency.get(currency, Decimal("0.00")) + amount
            )

        if not dry_run:
            await session.flush()

        return res
