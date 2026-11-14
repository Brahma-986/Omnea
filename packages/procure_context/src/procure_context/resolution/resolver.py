"""Entity resolution service with deterministic auto-link and fuzzy/embedding review banding."""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from procure_context.models import ResolutionResult, StageScores
from procure_context.resolution.embeddings import (
    EmbeddingProvider,
    HashEmbeddingProvider,
    cosine_similarity,
)
from procure_context.resolution.fuzzy import (
    block_candidates,
    fuzzy_score_candidate,
    hard_invalidation_check,
)
from procure_context.resolution.normalizer import NormalizedVendorName, normalize_vendor_name
from procure_core.models.vendor import (
    EntityReviewTaskModel,
    Neo4jOutboxModel,
    ResolutionDecisionModel,
    TenantResolutionSettingsModel,
    VendorAliasModel,
    VendorModel,
)

logger = logging.getLogger(__name__)


class EntityResolutionService:
    """Enterprise Entity Resolution Service executing the three-stage resolution pipeline."""

    def __init__(
        self,
        tenant_id: str,
        embedding_provider: EmbeddingProvider | None = None,
        high_threshold: float = 0.90,
        low_threshold: float = 0.75,
    ) -> None:
        self.tenant_id = tenant_id
        self.embedding_provider = embedding_provider or HashEmbeddingProvider()
        self.high_threshold = high_threshold
        self.low_threshold = low_threshold

    def _resolve_merge_pointer(
        self,
        vendor: dict[str, Any] | VendorModel,
        catalog_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[uuid.UUID, str]:
        """Follow merge chain at read-time to find the active survivor vendor."""
        curr = vendor
        hops = 0
        while hops < 4:
            if isinstance(curr, VendorModel):
                if curr.status != "merged" or not curr.merged_into_id:
                    return curr.id, curr.name
                # Follow pointer
                curr_merged_id = str(curr.merged_into_id)
            else:
                if curr.get("status") != "merged" or not curr.get("merged_into_id"):
                    raw_cid = curr["id"]
                    cid = uuid.UUID(raw_cid) if isinstance(raw_cid, str) else raw_cid
                    return cid, curr["name"]
                curr_merged_id = str(curr["merged_into_id"])

            if catalog_by_id and curr_merged_id in catalog_by_id:
                curr = catalog_by_id[curr_merged_id]
                hops += 1
            else:
                break

        vid = vendor.id if isinstance(vendor, VendorModel) else vendor["id"]
        vname = vendor.name if isinstance(vendor, VendorModel) else vendor["name"]
        return uuid.UUID(str(vid)), vname

    def resolve_against_catalog(
        self,
        raw_name: str,
        catalog: list[dict[str, Any]],
        aliases: list[dict[str, Any]] | None = None,
        catalog_variant: dict[str, Any] | None = None,
        embeddings_by_id: dict[str, list[float]] | None = None,
        force_embedding_outage: bool = False,
    ) -> ResolutionResult:
        """In-memory resolution against frozen catalog snapshot for benchmarking and evaluation."""
        norm: NormalizedVendorName = normalize_vendor_name(raw_name)

        # Apply catalog_variant per-case exclusions if specified
        active_catalog = list(catalog)
        if catalog_variant:
            excluded_names = set(catalog_variant.get("exclude", []))
            excluded_families = set(catalog_variant.get("exclude_families", []))
            active_catalog = [
                c
                for c in active_catalog
                if c["name"] not in excluded_names and c.get("family") not in excluded_families
            ]

        cat_by_id = {str(c["id"]): c for c in active_catalog}
        aliases_list = aliases or []

        # =====================================================================
        # Rule Path 1: Alias Lookup
        # =====================================================================
        norm_query_alias = norm.raw.strip().lower()
        matched_alias = None
        for a in aliases_list:
            if a["normalized_alias"] == norm_query_alias:
                matched_alias = a
                break

        if matched_alias and str(matched_alias["vendor_id"]) in cat_by_id:
            target_cand = cat_by_id[str(matched_alias["vendor_id"])]
            target_id, target_name = self._resolve_merge_pointer(target_cand, cat_by_id)
            if target_cand.get("status") == "pending_review":
                return ResolutionResult(
                    outcome="PROVISIONAL_REVIEW",
                    matched_vendor_id=target_id,
                    canonical_name=target_name,
                    confidence=1.0,
                    rule_path="fuzzy_review",
                    needs_review=True,
                    explanation=(
                        f"Alias '{matched_alias['alias']}' matched target in pending_review status"
                    ),
                    candidate_vendor_ids=[target_id],
                )
            return ResolutionResult(
                outcome="MATCH",
                matched_vendor_id=target_id,
                canonical_name=target_name,
                confidence=1.0,
                rule_path="alias",
                scores=StageScores(
                    jw_score=1.0, trigram_score=1.0, embedding_score=None, composite_score=1.0
                ),
                explanation=f"Exact alias match to '{matched_alias['alias']}'",
                candidate_vendor_ids=[target_id],
            )

        # =====================================================================
        # Rule Path 2: Exact Canonical Key Match
        # =====================================================================
        for cand in active_catalog:
            cand_key = cand.get("canonical_key")
            if not cand_key:
                c_norm = normalize_vendor_name(cand["name"])
                cand_key = c_norm.canonical_key

            if cand_key == norm.canonical_key:
                target_id, target_name = self._resolve_merge_pointer(cand, cat_by_id)
                if cand.get("status") == "pending_review":
                    return ResolutionResult(
                        outcome="PROVISIONAL_REVIEW",
                        matched_vendor_id=target_id,
                        canonical_name=target_name,
                        confidence=1.0,
                        rule_path="fuzzy_review",
                        needs_review=True,
                        explanation="Exact canonical key matched target in pending_review status",
                        candidate_vendor_ids=[target_id],
                    )
                return ResolutionResult(
                    outcome="MATCH",
                    matched_vendor_id=target_id,
                    canonical_name=target_name,
                    confidence=1.0,
                    rule_path="canonical_key",
                    scores=StageScores(
                        jw_score=1.0, trigram_score=1.0, embedding_score=None, composite_score=1.0
                    ),
                    explanation=f"Exact canonical key match to '{cand['name']}'",
                    candidate_vendor_ids=[target_id],
                )

        # =====================================================================
        # Rule Path 3: Exact Base + End-Region Parent Match
        # =====================================================================
        if norm.region is not None:
            # Look for existing entities sharing the exact normalized base
            matching_parents: list[dict[str, Any]] = []
            matching_siblings: list[dict[str, Any]] = []

            # Check alias table for parent matching norm.normalized_base
            parent_from_alias = None
            for a in aliases_list:
                if a["normalized_alias"] == norm.normalized_base:
                    if str(a["vendor_id"]) in cat_by_id:
                        parent_from_alias = cat_by_id[str(a["vendor_id"])]
                        break

            for cand in active_catalog:
                c_norm = normalize_vendor_name(cand["name"])
                if c_norm.canonical_key == norm.normalized_base:
                    if c_norm.region is None and cand.get("parent_id") is None:
                        matching_parents.append(cand)
                    else:
                        matching_siblings.append(cand)

            if not matching_parents and parent_from_alias:
                matching_parents.append(parent_from_alias)

            # If parent exists, create subsidiary
            if len(matching_parents) == 1:
                parent = matching_parents[0]
                target_id, target_name = self._resolve_merge_pointer(parent, cat_by_id)
                return ResolutionResult(
                    outcome="SUBSIDIARY_OF",
                    matched_vendor_id=target_id,
                    canonical_name=target_name,
                    confidence=1.0,
                    rule_path="exact_parent_region",
                    scores=StageScores(
                        jw_score=1.0, trigram_score=1.0, embedding_score=None, composite_score=1.0
                    ),
                    explanation=(
                        f"Regional subsidiary under parent '{parent['name']}' "
                        f"({norm.region.upper()})"
                    ),
                    candidate_vendor_ids=[target_id],
                )
            elif len(matching_parents) > 1 or (not matching_parents and len(matching_siblings) > 1):
                # Same-base parent tie! Multiple siblings exist without unique parent.
                sibling_ids = [
                    uuid.UUID(str(s["id"])) for s in (matching_parents or matching_siblings)
                ]
                return ResolutionResult(
                    outcome="PROVISIONAL_REVIEW",
                    matched_vendor_id=None,
                    canonical_name=None,
                    confidence=0.80,
                    rule_path="fuzzy_review",
                    needs_review=True,
                    explanation=(
                        f"Same-base parent tie for '{norm.normalized_base}'; "
                        f"requires human disambiguation"
                    ),
                    candidate_vendor_ids=sibling_ids,
                )
            elif not matching_parents and not matching_siblings:
                # Regional name with NO parent in catalog (e.g. Apex Nordic)
                return ResolutionResult(
                    outcome="PROVISIONAL_REVIEW",
                    matched_vendor_id=None,
                    canonical_name=None,
                    confidence=0.75,
                    rule_path="fuzzy_review",
                    needs_review=True,
                    explanation=(
                        f"Regional entity '{norm.raw}' with no existing parent "
                        f"'{norm.normalized_base}' in catalog"
                    ),
                    candidate_vendor_ids=[],
                )

        # =====================================================================
        # Rule Path 4: Allowlisted Loose Key Match
        # =====================================================================
        if norm.loose_key != norm.normalized_base:
            for cand in active_catalog:
                c_norm = normalize_vendor_name(cand["name"])
                if c_norm.canonical_key == norm.loose_key and cand.get("status") == "active":
                    target_id, target_name = self._resolve_merge_pointer(cand, cat_by_id)
                    return ResolutionResult(
                        outcome="MATCH",
                        matched_vendor_id=target_id,
                        canonical_name=target_name,
                        confidence=0.95,
                        rule_path="loose_key",
                        scores=StageScores(
                            jw_score=0.95,
                            trigram_score=0.90,
                            embedding_score=None,
                            composite_score=0.95,
                        ),
                        explanation=(
                            f"Matched parent '{cand['name']}' via allowlisted "
                            f"loose descriptor stripping"
                        ),
                        candidate_vendor_ids=[target_id],
                    )

        # =====================================================================
        # Stage 2: Candidate Blocking & Hard Invalidation Filtering
        # =====================================================================
        blocked_candidates = block_candidates(norm, active_catalog, min_trigram=0.15)

        surviving_candidates: list[tuple[dict[str, Any], float, float]] = []
        for cand in blocked_candidates:
            c_norm = normalize_vendor_name(cand["name"])
            is_blocked, _reason = hard_invalidation_check(
                norm, cand["name"], c_norm.canonical_key, c_norm.region
            )
            if is_blocked:
                continue

            jw, tri, _, _ = fuzzy_score_candidate(
                norm, c_norm.canonical_key, cand["name"], c_norm.region
            )
            surviving_candidates.append((cand, jw, tri))

        if not surviving_candidates:
            return ResolutionResult(
                outcome="NEW_ENTITY",
                matched_vendor_id=None,
                canonical_name=None,
                confidence=0.0,
                rule_path="low_confidence_new",
                explanation=(
                    "No surviving candidates passed candidate blocking and hard invalidation guards"
                ),
            )

        # =====================================================================
        # Stage 3: Embedding Reranking & Composite Scoring
        # =====================================================================
        # Handle embedding outage degradation
        degraded_reason: str | None = None
        query_vec: list[float] | None = None

        if not force_embedding_outage:
            try:
                # Compute projection for query via configured embedding provider
                if hasattr(self.embedding_provider, "_embed_single"):
                    query_vec = self.embedding_provider._embed_single(norm.normalized_base)
                else:
                    query_vec = HashEmbeddingProvider()._embed_single(norm.normalized_base)
            except Exception as e:
                logger.warning("Embedding service failure during resolution: %s", e)
                degraded_reason = "embedding_unavailable"
        else:
            degraded_reason = "embedding_unavailable"

        scored_records: list[tuple[dict[str, Any], StageScores]] = []
        for cand, jw, tri in surviving_candidates:
            emb_score = None
            if query_vec is not None and not degraded_reason:
                cand_vec = None
                if embeddings_by_id and str(cand["id"]) in embeddings_by_id:
                    cand_vec = embeddings_by_id[str(cand["id"])]
                elif hasattr(self.embedding_provider, "_embed_single"):
                    cand_vec = self.embedding_provider._embed_single(cand["name"])
                else:
                    cand_vec = HashEmbeddingProvider()._embed_single(cand["name"])
                emb_score = cosine_similarity(query_vec, cand_vec)

            if emb_score is not None:
                composite = 0.5 * jw + 0.5 * emb_score
            else:
                composite = jw  # Fallback to JW score when embeddings are unavailable

            scores = StageScores(
                jw_score=round(jw, 4),
                trigram_score=round(tri, 4),
                embedding_score=round(emb_score, 4) if emb_score is not None else None,
                composite_score=round(composite, 4),
            )
            scored_records.append((cand, scores))

        # Rank descending by composite score
        scored_records.sort(key=lambda x: x[1].composite_score, reverse=True)
        best_cand, best_scores = scored_records[0]
        ranked_cand_ids = [uuid.UUID(str(c["id"])) for c, _ in scored_records[:3]]

        # =====================================================================
        # Decision Policy Banding: Fuzzy and Embedding CAP at PROVISIONAL_REVIEW
        # =====================================================================
        if best_scores.composite_score >= self.low_threshold:
            target_id, target_name = self._resolve_merge_pointer(best_cand, cat_by_id)
            return ResolutionResult(
                outcome="PROVISIONAL_REVIEW",
                matched_vendor_id=target_id,
                canonical_name=target_name,
                confidence=round(best_scores.composite_score, 4),
                rule_path="fuzzy_review",
                scores=best_scores,
                needs_review=True,
                explanation=(
                    f"Fuzzy/embedding match to '{target_name}' with score "
                    f"{best_scores.composite_score:.4f}; capped at PROVISIONAL_REVIEW"
                ),
                degraded_reason=degraded_reason,
                candidate_vendor_ids=ranked_cand_ids,
            )
        else:
            return ResolutionResult(
                outcome="NEW_ENTITY",
                matched_vendor_id=None,
                canonical_name=None,
                confidence=round(best_scores.composite_score, 4),
                rule_path="low_confidence_new",
                scores=best_scores,
                explanation=(
                    f"Top candidate score {best_scores.composite_score:.4f} "
                    f"< low threshold {self.low_threshold}"
                ),
                degraded_reason=degraded_reason,
                candidate_vendor_ids=ranked_cand_ids,
            )

    async def resolve_transactional(
        self,
        session: AsyncSession,
        raw_name: str,
        commit_if_auto: bool = False,
        explain_only: bool = False,
    ) -> ResolutionResult:
        """Execute resolution against live Postgres database with advisory locks."""
        norm: NormalizedVendorName = normalize_vendor_name(raw_name)

        # 1. Acquire Family Advisory Lock
        family_lock_key = f"{self.tenant_id}:family:{norm.normalized_base}"
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": family_lock_key},
        )

        # Fetch active vendors and aliases for tenant
        res_vendors = await session.execute(
            select(VendorModel).where(VendorModel.tenant_id == self.tenant_id)
        )
        vendors = list(res_vendors.scalars().all())

        res_aliases = await session.execute(
            select(VendorAliasModel).where(VendorAliasModel.tenant_id == self.tenant_id)
        )
        aliases = list(res_aliases.scalars().all())

        cat_dicts = [
            {
                "id": str(v.id),
                "name": v.name,
                "canonical_key": v.canonical_key,
                "status": v.status,
                "parent_id": str(v.parent_vendor_id) if v.parent_vendor_id else None,
                "merged_into_id": str(v.merged_into_id) if v.merged_into_id else None,
                "family": v.normalized_base,
            }
            for v in vendors
        ]

        aliases_dicts = [
            {
                "id": str(a.id),
                "vendor_id": str(a.vendor_id),
                "alias": a.alias,
                "normalized_alias": a.normalized_alias,
            }
            for a in aliases
        ]

        result = self.resolve_against_catalog(raw_name, cat_dicts, aliases_dicts)

        if explain_only:
            # In explain-only mode, return resolution result without DB mutation
            return result

        # Get or create tenant resolution settings
        q_s = await session.execute(
            select(TenantResolutionSettingsModel).where(
                and_(
                    TenantResolutionSettingsModel.tenant_id == self.tenant_id,
                    TenantResolutionSettingsModel.is_active == True,  # noqa: E712
                )
            )
        )
        settings = q_s.scalar_one_or_none()
        if not settings:
            settings = TenantResolutionSettingsModel(
                tenant_id=self.tenant_id,
                version="v1.0",
                high_priority_threshold=Decimal("0.880"),
                review_threshold=Decimal("0.650"),
                weight_jw=Decimal("0.500"),
                weight_emb=Decimal("0.500"),
                is_active=True,
            )
            session.add(settings)
            await session.flush()

        outcome_db = result.outcome.lower()
        rule_path_db = result.rule_path.lower()
        allowed_rule_paths = {
            "alias",
            "canonical_key",
            "exact_parent_region",
            "loose_key",
            "strong_identifier",
            "fuzzy_review",
            "embedding_review",
            "low_confidence_new",
        }
        if rule_path_db not in allowed_rule_paths:
            rule_path_db = "fuzzy_review" if "review" in rule_path_db else "low_confidence_new"

        # Record resolution decision in database
        decision = ResolutionDecisionModel(
            tenant_id=self.tenant_id,
            settings_id=settings.id,
            resolver_version="v1.0",
            threshold_set_version=settings.version,
            rule_path=rule_path_db,
            input_name=raw_name,
            normalized_form=norm.normalized_base,
            canonical_key=norm.canonical_key,
            matched_vendor_id=result.matched_vendor_id,
            outcome=outcome_db,
            composite_score=Decimal(str(round(result.scores.composite_score, 4))),
            degraded_reason=result.degraded_reason,
            needs_rescoring=result.degraded_reason is not None,
            component_scores=result.scores.model_dump(),
            candidates=[str(cid) for cid in result.candidate_vendor_ids],
        )
        session.add(decision)
        await session.flush()
        result.decision_id = decision.id

        # If provisional review, queue EntityReviewTaskModel
        if result.outcome == "PROVISIONAL_REVIEW":
            # Create provisional vendor row
            provisional_key = f"prov:{norm.canonical_key}:{uuid.uuid4().hex[:8]}"
            prov_vendor = VendorModel(
                tenant_id=self.tenant_id,
                canonical_key=provisional_key,
                name=raw_name,
                normalized_base=norm.normalized_base,
                region=norm.region,
                status="pending_review",
            )
            session.add(prov_vendor)
            await session.flush()

            # Check review volume queue depth and cap
            q_cnt = await session.execute(
                select(func.count(EntityReviewTaskModel.id)).where(
                    and_(
                        EntityReviewTaskModel.tenant_id == self.tenant_id,
                        EntityReviewTaskModel.status == "pending",
                    )
                )
            )
            pending_queue_depth = q_cnt.scalar_one() or 0
            review_cap = 500  # Default human review queue volume cap
            cap_exceeded = pending_queue_depth >= review_cap
            if cap_exceeded:
                logger.warning(
                    "Review queue volume cap reached (%d >= %d) for tenant %s",
                    pending_queue_depth,
                    review_cap,
                    self.tenant_id,
                )

            # Check for existing review task on (tenant, provisional_vendor, candidate_vendor)
            q_task = await session.execute(
                select(EntityReviewTaskModel).where(
                    and_(
                        EntityReviewTaskModel.tenant_id == self.tenant_id,
                        EntityReviewTaskModel.provisional_vendor_id == prov_vendor.id,
                        EntityReviewTaskModel.candidate_vendor_id == result.matched_vendor_id,
                    )
                )
            )
            task_metadata = {
                "candidate_ids": [str(cid) for cid in result.candidate_vendor_ids],
                "queue_depth": pending_queue_depth,
                "review_volume_cap": review_cap,
                "cap_exceeded": cap_exceeded,
            }
            review_task = q_task.scalar_one_or_none()
            if review_task:
                review_task.confidence = Decimal(str(round(result.confidence, 4)))
                review_task.component_scores = result.scores.model_dump()
                review_task.metadata_ = task_metadata
            else:
                review_task = EntityReviewTaskModel(
                    tenant_id=self.tenant_id,
                    task_type="duplicate_review",
                    provisional_vendor_id=prov_vendor.id,
                    candidate_vendor_id=result.matched_vendor_id,
                    source_name=raw_name,
                    confidence=Decimal(str(round(result.confidence, 4))),
                    component_scores=result.scores.model_dump(),
                    status="pending",
                    metadata_=task_metadata,
                )
                session.add(review_task)
            await session.flush()
            result.review_task_id = review_task.id

        elif result.outcome in ("MATCH", "SUBSIDIARY_OF") and commit_if_auto:
            # Enqueue Neo4j Outbox event for auto-link
            outbox = Neo4jOutboxModel(
                tenant_id=self.tenant_id,
                aggregate_id=str(result.matched_vendor_id),
                ordering_key=f"vendor:{result.matched_vendor_id}",
                idempotency_key=f"autolink:{decision.id}",
                event_type="vendor_linked",
                payload={
                    "vendor_id": str(result.matched_vendor_id),
                    "outcome": result.outcome,
                    "raw_query": raw_name,
                },
            )
            session.add(outbox)

        return result

    resolve = resolve_transactional
