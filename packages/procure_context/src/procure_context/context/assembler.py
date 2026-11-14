"""Enterprise Context Assembler for Graph RAG retrieval."""

from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from procure_context.graph.repository import GraphRepository
from procure_context.models import (
    ActiveContractSummary,
    EnterpriseContext,
    SecurityReviewSummary,
    SpendSummary,
)
from procure_context.resolution.resolver import EntityResolutionService
from procure_core.models.base import Base  # noqa: F401
from procure_core.models.intent import ProcurementIntentModel
from procure_core.models.request import ApprovalModel, RequestModel
from procure_core.models.vendor import (
    ContractModel,
    IntentVendorResolutionModel,
    Neo4jOutboxModel,
    SpendTransactionModel,
    VendorModel,
    VendorSecurityReviewModel,
)

logger = logging.getLogger(__name__)


class ContextAssembler:
    """Assembles rich context combining graph topology and PostgreSQL ledgers."""

    def __init__(self, tenant_id: str, graph_repo: GraphRepository | None = None) -> None:
        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string for ContextAssembler")
        self.tenant_id = tenant_id
        self.graph_repo = graph_repo

    async def _resolve_hierarchy_postgres(
        self,
        session: AsyncSession,
        vendor_id: uuid.UUID,
    ) -> dict[str, Any]:
        """PostgreSQL recursive CTE fallback to resolve vendor hierarchy and follow merge chains."""
        # 1. Follow merge pointers to find active survivor
        curr_id = vendor_id
        was_merged = False
        merge_hops = 0

        while merge_hops < 5:
            q_v = await session.execute(
                select(VendorModel).where(
                    and_(
                        VendorModel.tenant_id == self.tenant_id,
                        VendorModel.id == curr_id,
                    )
                )
            )
            v = q_v.scalar_one_or_none()
            if not v:
                break
            if v.status == "merged" and v.merged_into_id:
                curr_id = v.merged_into_id
                was_merged = True
                merge_hops += 1
            else:
                break

        effective_id = curr_id
        q_eff = await session.execute(
            select(VendorModel).where(
                and_(
                    VendorModel.tenant_id == self.tenant_id,
                    VendorModel.id == effective_id,
                )
            )
        )
        eff_vendor = q_eff.scalar_one_or_none()
        if not eff_vendor:
            return {
                "effective_vendor": None,
                "was_merged": was_merged,
                "parent_vendor": None,
                "subsidiary_ids": [],
                "merged_vendor_ids": [],
            }

        # 2. Find parent vendor if exists
        parent_vendor = None
        if eff_vendor.parent_vendor_id:
            q_p = await session.execute(
                select(VendorModel).where(
                    and_(
                        VendorModel.tenant_id == self.tenant_id,
                        VendorModel.id == eff_vendor.parent_vendor_id,
                    )
                )
            )
            parent_vendor = q_p.scalar_one_or_none()

        # 3. Find recursive subsidiaries (depth <= 4)
        sub_query = text("""
            WITH RECURSIVE subs AS (
                SELECT id, parent_vendor_id, 1 AS depth
                FROM vendors
                WHERE parent_vendor_id = :eff_id
                  AND tenant_id = :tenant_id
                UNION ALL
                SELECT v.id, v.parent_vendor_id, s.depth + 1
                FROM vendors v
                JOIN subs s ON v.parent_vendor_id = s.id
                WHERE s.depth < 4
                  AND v.tenant_id = :tenant_id
            )
            SELECT id FROM subs;
        """)
        sub_res = await session.execute(
            sub_query, {"eff_id": effective_id, "tenant_id": self.tenant_id}
        )
        subsidiary_ids = [row[0] for row in sub_res.fetchall()]

        # 4. Find all vendors merged into this active survivor
        merged_query = text("""
            WITH RECURSIVE merged AS (
                SELECT id, merged_into_id
                FROM vendors
                WHERE merged_into_id = :eff_id
                  AND tenant_id = :tenant_id
                UNION ALL
                SELECT v.id, v.merged_into_id
                FROM vendors v
                JOIN merged m ON v.merged_into_id = m.id
                WHERE v.tenant_id = :tenant_id
            )
            SELECT id FROM merged;
        """)
        merged_res = await session.execute(
            merged_query, {"eff_id": effective_id, "tenant_id": self.tenant_id}
        )
        merged_vendor_ids = [row[0] for row in merged_res.fetchall()]

        return {
            "effective_vendor": eff_vendor,
            "was_merged": was_merged,
            "parent_vendor": parent_vendor,
            "subsidiary_ids": subsidiary_ids,
            "merged_vendor_ids": merged_vendor_ids,
        }

    async def assemble_context(
        self,
        session: AsyncSession,
        vendor_id: uuid.UUID,
        as_of_date: date | None = None,
    ) -> EnterpriseContext:
        """Assemble full context for a vendor across Neo4j graph and PostgreSQL ledgers."""
        ref_date = as_of_date or date.today()
        citations: list[dict[str, Any]] = []

        # 1. Resolve hierarchy (try Neo4j first if graph_repo available, fallback to Postgres)
        subsidiary_ids: list[uuid.UUID] = []
        merged_vendor_ids: list[uuid.UUID] = []
        was_merged = False
        effective_id = vendor_id

        if self.graph_repo is not None:
            try:
                neo_hier = await self.graph_repo.get_vendor_hierarchy(vendor_id)
                if neo_hier.get("name"):
                    effective_id = neo_hier["vendor_id"]
                    was_merged = bool(neo_hier.get("was_merged"))
                    subsidiary_ids = list(neo_hier.get("subsidiary_ids", []))
                    merged_vendor_ids = list(neo_hier.get("merged_vendor_ids", []))
            except Exception as e:
                logger.warning("Neo4j hierarchy query failed, falling back to PostgreSQL: %s", e)

        # Query Postgres for authoritative vendor record
        pg_hier = await self._resolve_hierarchy_postgres(session, effective_id)
        eff_vendor = pg_hier["effective_vendor"]
        if not eff_vendor:
            # If still not found, check original vendor_id directly
            q_v = await session.execute(
                select(VendorModel).where(
                    and_(
                        VendorModel.tenant_id == self.tenant_id,
                        VendorModel.id == vendor_id,
                    )
                )
            )
            eff_vendor = q_v.scalar_one_or_none()

        if not eff_vendor:
            raise ValueError(f"Vendor {vendor_id} not found in tenant {self.tenant_id}")

        # Combine hierarchy discovered via graph and DB
        if pg_hier["was_merged"]:
            was_merged = True
        subsidiary_ids = sorted(set(subsidiary_ids + pg_hier["subsidiary_ids"]))
        merged_vendor_ids = sorted(set(merged_vendor_ids + pg_hier["merged_vendor_ids"]))

        parent_vendor = pg_hier["parent_vendor"]
        parent_id = parent_vendor.id if parent_vendor else eff_vendor.parent_vendor_id
        parent_name = parent_vendor.name if parent_vendor else None

        if was_merged:
            citations.append(
                {
                    "source_table": "vendors",
                    "fact": (
                        f"Requested vendor {vendor_id} was merged into survivor "
                        f"{eff_vendor.name} ({eff_vendor.id})"
                    ),
                    "requested_vendor_id": str(vendor_id),
                    "effective_vendor_id": str(eff_vendor.id),
                    "as_of": ref_date.isoformat(),
                }
            )

        citations.append(
            {
                "source_table": "vendors",
                "fact": (
                    f"Vendor '{eff_vendor.name}' has parent='{parent_name}', "
                    f"{len(subsidiary_ids)} subsidiary entities, and "
                    f"{len(merged_vendor_ids)} merged vendors"
                ),
                "vendor_id": str(eff_vendor.id),
                "subsidiary_count": len(subsidiary_ids),
                "merged_count": len(merged_vendor_ids),
                "as_of": ref_date.isoformat(),
            }
        )

        # All relevant vendor IDs for spend rollup, contracts, and security reviews
        all_family_ids = sorted(set([eff_vendor.id] + subsidiary_ids + merged_vendor_ids))

        # 2. Spend Rollup (Strictly grouped by currency, never summed across currencies)
        spend_q = (
            select(
                SpendTransactionModel.currency,
                func.sum(SpendTransactionModel.amount).label("total_spend"),
                func.count().label("transaction_count"),
                func.min(SpendTransactionModel.transaction_date).label("first_transaction_date"),
                func.max(SpendTransactionModel.transaction_date).label("last_transaction_date"),
            )
            .where(
                and_(
                    SpendTransactionModel.tenant_id == self.tenant_id,
                    SpendTransactionModel.vendor_id.in_(all_family_ids),
                )
            )
            .group_by(SpendTransactionModel.currency)
            .order_by(text("total_spend DESC"))
        )
        spend_res = await session.execute(spend_q)
        spend_by_currency: list[SpendSummary] = []

        for row in spend_res.fetchall():
            cur, total, count, first_d, last_d = row
            spend_summary = SpendSummary(
                currency=cur,
                total_spend=Decimal(str(total or 0)),
                transaction_count=int(count),
                first_transaction_date=first_d,
                last_transaction_date=last_d,
            )
            spend_by_currency.append(spend_summary)
            citations.append(
                {
                    "source_table": "spend_transactions",
                    "fact": (
                        f"Total spend {spend_summary.total_spend} {spend_summary.currency} "
                        f"across {spend_summary.transaction_count} transactions across "
                        f"{len(all_family_ids)} related entity/subsidiaries"
                    ),
                    "currency": spend_summary.currency,
                    "total_spend": str(spend_summary.total_spend),
                    "transaction_count": spend_summary.transaction_count,
                    "as_of": ref_date.isoformat(),
                }
            )

        if not spend_by_currency:
            citations.append(
                {
                    "source_table": "spend_transactions",
                    "fact": "Spend: none_recorded",
                    "as_of": ref_date.isoformat(),
                }
            )

        # 3. Active Contracts (status == 'active' and end_date >= ref_date)
        contract_q = (
            select(ContractModel)
            .where(
                and_(
                    ContractModel.tenant_id == self.tenant_id,
                    ContractModel.vendor_id.in_(all_family_ids),
                    ContractModel.status == "active",
                    ContractModel.end_date != None,  # noqa: E711
                    ContractModel.end_date >= ref_date,
                )
            )
            .order_by(ContractModel.start_date.desc())
        )
        contract_res = await session.execute(contract_q)
        active_contracts: list[ActiveContractSummary] = []

        for c in contract_res.scalars().all():
            summary = ActiveContractSummary(
                contract_id=c.id,
                title=c.title,
                total_value=c.total_value,
                currency=c.currency,
                start_date=c.start_date,
                end_date=c.end_date,
                auto_renew=c.auto_renew,
            )
            active_contracts.append(summary)
            citations.append(
                {
                    "source_table": "contracts",
                    "fact": (
                        f"Active contract '{c.title}' (ID: {c.id}) valued at "
                        f"{c.total_value} {c.currency}, active until {c.end_date}"
                    ),
                    "contract_id": str(c.id),
                    "total_value": str(c.total_value),
                    "currency": c.currency,
                    "as_of": ref_date.isoformat(),
                }
            )

        if not active_contracts:
            citations.append(
                {
                    "source_table": "contracts",
                    "fact": "Contracts: none_recorded",
                    "as_of": ref_date.isoformat(),
                }
            )

        # 4. Security Reviews & Freshness (SOC2 freshness from audit period; missing date = unknown)
        sec_q = (
            select(VendorSecurityReviewModel)
            .where(
                and_(
                    VendorSecurityReviewModel.tenant_id == self.tenant_id,
                    VendorSecurityReviewModel.vendor_id.in_(all_family_ids),
                )
            )
            .order_by(VendorSecurityReviewModel.expiration_date.desc())
        )
        sec_res = await session.execute(sec_q)
        security_reviews: list[SecurityReviewSummary] = []

        for s in sec_res.scalars().all():
            audit_period_end = (s.metadata_ or {}).get("audit_period_end")
            effective_exp = s.expiration_date

            if effective_exp is None and audit_period_end is None:
                freshness_status = "unknown"
                is_fresh = False
            elif (
                effective_exp is not None and s.status == "compliant" and effective_exp >= ref_date
            ):
                freshness_status = "compliant"
                is_fresh = True
            elif effective_exp is not None and effective_exp < ref_date:
                freshness_status = "expired"
                is_fresh = False
            else:
                freshness_status = s.status
                is_fresh = False

            sec_summary = SecurityReviewSummary(
                doc_type=s.doc_type,
                status=s.status,
                effective_date=s.effective_date,
                expiration_date=s.expiration_date,
                audit_period_end=audit_period_end,
                freshness_status=freshness_status,
                is_fresh=is_fresh,
            )
            security_reviews.append(sec_summary)
            citations.append(
                {
                    "source_table": "vendor_security_reviews",
                    "fact": (
                        f"Security certification {s.doc_type} status '{s.status}' "
                        f"effective {s.effective_date} to {s.expiration_date} "
                        f"(freshness_status={freshness_status}, fresh={is_fresh})"
                    ),
                    "doc_type": s.doc_type,
                    "status": s.status,
                    "freshness_status": freshness_status,
                    "is_fresh": is_fresh,
                    "as_of": ref_date.isoformat(),
                }
            )

        if not security_reviews:
            citations.append(
                {
                    "source_table": "vendor_security_reviews",
                    "fact": "Security reviews: not_checked",
                    "as_of": ref_date.isoformat(),
                }
            )

        # 5. Prior Approvals Count
        # Check direct vendor_id on approvals, or via intent resolutions linked to request approvals
        app_q = (
            select(func.count(func.distinct(ApprovalModel.id)))
            .outerjoin(
                ProcurementIntentModel,
                and_(
                    ProcurementIntentModel.request_id == ApprovalModel.request_id,
                    ProcurementIntentModel.tenant_id == ApprovalModel.tenant_id,
                ),
            )
            .outerjoin(
                IntentVendorResolutionModel,
                and_(
                    IntentVendorResolutionModel.intent_id == ProcurementIntentModel.id,
                    IntentVendorResolutionModel.tenant_id == ProcurementIntentModel.tenant_id,
                ),
            )
            .where(
                and_(
                    ApprovalModel.tenant_id == self.tenant_id,
                    ApprovalModel.status == "approved",
                    (
                        ApprovalModel.vendor_id.in_(all_family_ids)
                        | IntentVendorResolutionModel.vendor_id.in_(all_family_ids)
                    ),
                )
            )
        )
        app_res = await session.execute(app_q)
        prior_approvals = int(app_res.scalar() or 0)

        # Also check requests table payload if any
        str_family_ids = [str(fid) for fid in all_family_ids]
        req_q = (
            select(func.count(func.distinct(ApprovalModel.id)))
            .join(
                RequestModel,
                and_(
                    RequestModel.id == ApprovalModel.request_id,
                    RequestModel.tenant_id == ApprovalModel.tenant_id,
                ),
            )
            .where(
                and_(
                    ApprovalModel.tenant_id == self.tenant_id,
                    ApprovalModel.status == "approved",
                    RequestModel.payload["vendor_id"].astext.in_(str_family_ids),
                )
            )
        )
        try:
            req_res = await session.execute(req_q)
            req_approvals = int(req_res.scalar() or 0)
            prior_approvals = max(prior_approvals, req_approvals)
        except Exception:
            pass

        if prior_approvals > 0:
            citations.append(
                {
                    "source_table": "approvals",
                    "fact": (
                        f"Found {prior_approvals} prior approved procurement requests for "
                        "vendor family"
                    ),
                    "count": prior_approvals,
                    "as_of": ref_date.isoformat(),
                }
            )

        # 6. Compute Neo4j projection lag for this tenant
        outbox_q = select(func.count(Neo4jOutboxModel.id)).where(
            and_(
                Neo4jOutboxModel.tenant_id == self.tenant_id,
                Neo4jOutboxModel.status.in_(["pending", "processing"]),
            )
        )
        outbox_res = await session.execute(outbox_q)
        projection_lag = int(outbox_res.scalar() or 0)

        return EnterpriseContext(
            vendor_id=eff_vendor.id,
            vendor_name=eff_vendor.name,
            canonical_key=eff_vendor.canonical_key,
            status=eff_vendor.status,
            parent_vendor_id=parent_id,
            parent_vendor_name=parent_name,
            subsidiary_vendor_ids=subsidiary_ids,
            merged_vendor_ids=merged_vendor_ids,
            spend_by_currency=spend_by_currency,
            active_contracts=active_contracts,
            prior_approvals_count=prior_approvals,
            security_reviews=security_reviews,
            hierarchy_source="postgres_authoritative",
            projection_lag=projection_lag,
            citations=citations,
        )

    async def assemble_context_for_intent(
        self,
        session: AsyncSession,
        intent: ProcurementIntentModel,
        as_of_date: date | None = None,
    ) -> EnterpriseContext:
        """Assemble context given a ProcurementIntent by resolving its vendor."""
        # 1. Check if vendor resolution already recorded in intent_vendor_resolutions
        res_q = await session.execute(
            select(IntentVendorResolutionModel).where(
                and_(
                    IntentVendorResolutionModel.tenant_id == self.tenant_id,
                    IntentVendorResolutionModel.intent_id == intent.id,
                    IntentVendorResolutionModel.role == "vendor_name",
                )
            )
        )
        resolution = res_q.scalar_one_or_none()

        vendor_id = resolution.vendor_id if resolution else None

        # 2. If not yet resolved, use EntityResolutionService
        if not vendor_id and intent.vendor_name:
            resolver = EntityResolutionService(self.tenant_id)
            res = await resolver.resolve_transactional(session, intent.vendor_name)
            vendor_id = res.matched_vendor_id

        # 3. If resolved to an existing vendor, assemble full context
        if vendor_id:
            return await self.assemble_context(session, vendor_id, as_of_date=as_of_date)

        # 4. If novel entity or unresolvable, return provisional new context
        return EnterpriseContext(
            vendor_id=uuid.uuid4(),
            vendor_name=intent.vendor_name or "Unknown Vendor",
            canonical_key=intent.vendor_name or "unknown",
            status="novel_entity",
            citations=[
                {
                    "source_table": "procurement_intents",
                    "fact": (
                        f"Intent {intent.id} vendor '{intent.vendor_name}' "
                        "does not match existing catalog"
                    ),
                }
            ],
        )
