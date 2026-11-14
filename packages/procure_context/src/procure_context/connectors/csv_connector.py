"""CSV batch ingestion connector with customizable column mapping."""

from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from procure_context.connectors.base import BaseConnector, IngestionResult, IngestionRow
from procure_context.resolution.resolver import EntityResolutionService

logger = logging.getLogger(__name__)

DEFAULT_COLUMN_MAPPING = {
    "source_id": "source_id",
    "vendor_name": "vendor_name",
    "amount": "amount",
    "currency": "currency",
    "transaction_date": "transaction_date",
    "category": "category",
}


def _parse_amount(amt_str: str) -> Decimal:
    """Parse numeric amount from currency string, defaulting to 0.00 on malformed input."""
    try:
        clean = amt_str.replace("$", "").replace(",", "").strip()
        return Decimal(clean) if clean else Decimal("0.00")
    except Exception:
        return Decimal("0.00")


def _parse_date(date_str: str) -> date:
    """Parse string date from common formats."""
    clean = date_str.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(clean, fmt).date()
        except ValueError:
            continue
    return date.today()


class CsvConnector(BaseConnector):
    """Imports batch vendor spend from CSV data with per-source column mapping."""

    def __init__(
        self,
        tenant_id: str,
        source_system: str = "csv_import",
        column_mapping: dict[str, str] | None = None,
        resolver: EntityResolutionService | None = None,
    ) -> None:
        super().__init__(tenant_id, source_system, resolver)
        self.column_mapping = column_mapping or DEFAULT_COLUMN_MAPPING

    async def ingest_csv(
        self,
        session: AsyncSession,
        csv_data: str,
        dry_run: bool = False,
    ) -> IngestionResult:
        """Ingest CSV string, resolve vendors, record spend, and return execution report."""
        f = io.StringIO(csv_data.strip())
        reader = csv.DictReader(f)

        res = IngestionResult(dry_run=dry_run)
        inv_map = {v: k for k, v in self.column_mapping.items()}

        for row_idx, raw_row in enumerate(reader):
            res.total_rows += 1
            std_row: dict[str, Any] = {}
            for col, val in raw_row.items():
                if col in inv_map:
                    std_row[inv_map[col]] = val.strip() if val else ""

            v_name = std_row.get("vendor_name", "").strip()
            if not v_name:
                continue

            amount = _parse_amount(std_row.get("amount", "0"))
            currency = std_row.get("currency", "USD").strip().upper()
            src_id = std_row.get("source_id") or f"row-{row_idx + 1}"
            tx_date = _parse_date(std_row.get("transaction_date", ""))
            category = std_row.get("category", "general")

            ing_row = IngestionRow(
                source_id=src_id,
                vendor_name=v_name,
                amount=amount,
                currency=currency,
                transaction_date=tx_date,
                category=category,
                provenance={"csv_line": row_idx + 1, "raw_data": raw_row},
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
