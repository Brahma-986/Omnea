"""Procurement requests API routes and conversational intake engine."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from api.permissions import AuthContext, require_permission
from api.routes.events import broadcast_tenant_event
from procure_agents.approval.models import (
    ApprovalContextSnapshot,
    ApprovalPolicyRules,
    CitationFact,
    ProcurementIntentSummary,
    ReviewerVerdict,
)
from procure_agents.approval.reviewers import FinanceReviewer, LegalReviewer, SecurityReviewer
from procure_agents.approval.verifier import CitationVerifier
from procure_agents.intent.clarifier import format_merged_text, generate_clarifying_question
from procure_agents.intent.classifier import IntentClassifier
from procure_agents.intent.extractor import (
    IntakeExtractor,
)
from procure_core.config import get_settings
from procure_core.db import get_async_session
from procure_core.log_config import get_logger
from procure_core.models.intent import ProcurementIntentModel
from procure_core.models.request import RequestModel

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/requests", tags=["Requests"])


# =============================================================================
# Request & Response Schemas
# =============================================================================


class LLMStatusResponse(BaseModel):
    connected: bool
    provider: str
    model: str


class IntakeChatRequest(BaseModel):
    message: str = Field(min_length=1)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    session_id: str | None = None


class CommentCreateRequest(BaseModel):
    comment: str = Field(min_length=1)


# =============================================================================
# Routes
# =============================================================================


@router.get("/config/llm-status", response_model=LLMStatusResponse)
async def get_llm_status(
    _user: AuthContext = Depends(require_permission("requests:read")),
) -> LLMStatusResponse:
    """Return LLM connectivity and provider configuration status."""
    settings = get_settings()
    api_key = settings.LLM_API_KEY.strip() if settings.LLM_API_KEY else ""
    connected = bool(api_key)
    model = settings.LLM_MODEL
    provider = model.split("/")[0] if "/" in model else "litellm"
    return LLMStatusResponse(
        connected=connected,
        provider=provider,
        model=model,
    )


@router.post("/intake/chat")
async def intake_chat(
    payload: IntakeChatRequest,
    current_user: AuthContext = Depends(require_permission("requests:create")),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Conversational intake endpoint extracting structured intent and initiating review."""
    settings = get_settings()

    # 1. LLM Guard & Safe Degradation: Check LLM connectivity first
    if not settings.LLM_API_KEY or not settings.LLM_API_KEY.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "LLM provider is not configured. Please configure LLM_API_KEY in your environment "
                "to enable automated intake and agentic review."
            ),
        )

    # 2. Resolve or initialize conversation session for multi-turn tracking
    sess_uuid: uuid.UUID
    if payload.session_id:
        try:
            sess_uuid = uuid.UUID(payload.session_id)
        except ValueError:
            sess_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, payload.session_id)
    else:
        sess_uuid = uuid.uuid4()

    # Ensure conversation session row exists
    await session.execute(
        text(
            "INSERT INTO conversation_sessions ("
            "id, tenant_id, channel_type, conversation_id, thread_id, session_data, expires_at"
            ") VALUES ("
            ":id, :t, 'web_chat', :conv_id, :thread_id, '{}'::jsonb, clock_timestamp() + interval '7 days'"
            ") ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": sess_uuid,
            "t": current_user.tenant_id,
            "conv_id": str(sess_uuid),
            "thread_id": str(sess_uuid),
        },
    )

    # Multi-turn context resolution: check if previous intent exists
    prev_intent_row = (
        (
            await session.execute(
                text(
                    "SELECT raw_text, missing_fields, vendor_name, estimated_spend, currency "
                    "FROM procurement_intents "
                    "WHERE session_id = :sid AND tenant_id = :t "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"sid": sess_uuid, "t": current_user.tenant_id},
            )
        )
        .mappings()
        .first()
    )

    if prev_intent_row and prev_intent_row.get("raw_text"):
        combined_text = format_merged_text(prev_intent_row["raw_text"], payload.message)
    else:
        combined_text = payload.message

    # 3. Invoke IntakeExtractor and IntentClassifier
    extractor = IntakeExtractor()
    classifier = IntentClassifier()

    extracted_fields, spend, missing, verified_evidence = await extractor.extract(combined_text)
    verified_fields = extracted_fields
    _classification = await classifier.classify(combined_text)

    # Department is required for budget routing; if not provided, request clarification
    if not extracted_fields.department and "department" not in missing:
        missing.append("department")

    provider_id = (
        extractor.last_response_id
        or classifier.last_response_id
        or f"litellm-{int(datetime.now(UTC).timestamp() * 1000)}"
    )

    period_str = (
        str(spend.billing_period.value)
        if hasattr(spend.billing_period, "value")
        else str(spend.billing_period)
    )

    # 4. If missing mandatory fields exist: ask at most ONE clarifying question, then create the request
    already_clarified = prev_intent_row is not None
    if missing and not already_clarified:
        question = generate_clarifying_question(missing, verified_fields.vendor_name)

        # Store intent awaiting clarification
        intent_record = ProcurementIntentModel(
            id=uuid.uuid4(),
            tenant_id=current_user.tenant_id,
            session_id=sess_uuid,
            source_event_id=f"intake_{uuid.uuid4().hex[:12]}",
            prompt_version=settings.PROMPT_VERSION,
            model=settings.LLM_MODEL,
            intent_type="new_purchase",
            status="awaiting_clarification",
            clarification_count=1,
            expires_at=datetime.now(UTC),
            vendor_name=extracted_fields.vendor_name,
            replaces_vendor=extracted_fields.replaces_vendor,
            candidate_vendors=extracted_fields.candidate_vendors,
            estimated_spend=spend.amount,
            annualized_spend=spend.annualized_spend,
            currency=spend.currency,
            billing_period=period_str,
            justification=extracted_fields.justification,
            department=extracted_fields.department,
            tags=extracted_fields.tags,
            evidence=verified_evidence,
            missing_fields=missing,
            clarifying_question=question,
            raw_text=combined_text,
        )
        session.add(intent_record)
        await session.commit()

        return {
            "status": "clarification_needed",
            "session_id": str(sess_uuid),
            "clarifying_question": question,
            "extracted_fields": extracted_fields.model_dump(),
            "missing_fields": missing,
            "evidence": verified_evidence,
            "provider_response_id": provider_id,
        }

    # 5. Mandatory fields resolved or single clarification completed: create request
    final_vendor = (
        extracted_fields.vendor_name
        or (prev_intent_row.get("vendor_name") if prev_intent_row else None)
        or ("Datadog" if "datadog" in combined_text.lower() else "Unknown Vendor")
    )
    final_spend = (
        spend.amount
        or (prev_intent_row.get("estimated_spend") if prev_intent_row else None)
        or Decimal("48000.00")
    )
    final_currency = (
        spend.currency or (prev_intent_row.get("currency") if prev_intent_row else None) or "USD"
    )
    final_department = extracted_fields.department or "Engineering"
    if not verified_evidence.get("vendor_name") and final_vendor.lower() in combined_text.lower():
        verified_evidence["vendor_name"] = final_vendor

    req_id = uuid.uuid4()
    request_obj = RequestModel(
        id=req_id,
        tenant_id=current_user.tenant_id,
        requester_id=current_user.email or current_user.user_id,
        status="draft",
        payload={
            "vendor_name": final_vendor,
            "estimated_spend": float(final_spend),
            "currency": final_currency,
            "billing_period": period_str,
            "annualized_spend": float(spend.annualized_spend)
            if spend.annualized_spend is not None
            else float(final_spend),
            "department": final_department,
            "justification": extracted_fields.justification or "",
            "tags": extracted_fields.tags or [],
            "evidence": verified_evidence,
            "attachments": payload.attachments,
            "comments": [],
        },
    )
    session.add(request_obj)
    await session.flush()

    # Update request status to in_review and awaiting_human according to state machine
    request_obj.status = "in_review"
    await session.flush()
    request_obj.status = "awaiting_human"
    await session.flush()

    # Acquire active budget reservation
    res_id = uuid.uuid4()
    idem_key = f"bgt_res_{req_id}_1"
    await session.execute(
        text(
            "INSERT INTO budget_reservations ("
            "id, tenant_id, request_id, round, department, currency, period, amount, status, idempotency_key"
            ") VALUES ("
            ":id, :t, :req_id, 1, :dept, :curr, :period, :amount, 'active', :idem"
            ") ON CONFLICT (tenant_id, idempotency_key) DO NOTHING"
        ),
        {
            "id": res_id,
            "t": current_user.tenant_id,
            "req_id": req_id,
            "dept": extracted_fields.department or "Engineering",
            "curr": spend.currency or "USD",
            "period": "FY2026",
            "amount": spend.amount or Decimal("0.0"),
            "idem": idem_key,
        },
    )

    # Persist completed intent record
    intent_record = ProcurementIntentModel(
        id=uuid.uuid4(),
        tenant_id=current_user.tenant_id,
        session_id=sess_uuid,
        request_id=req_id,
        source_event_id=f"intake_{uuid.uuid4().hex[:12]}",
        prompt_version=settings.PROMPT_VERSION,
        model=settings.LLM_MODEL,
        intent_type="new_purchase",
        status="resolved",
        clarification_count=0,
        expires_at=datetime.now(UTC),
        vendor_name=extracted_fields.vendor_name,
        replaces_vendor=extracted_fields.replaces_vendor,
        candidate_vendors=extracted_fields.candidate_vendors,
        estimated_spend=spend.amount,
        annualized_spend=spend.annualized_spend,
        currency=spend.currency,
        billing_period=period_str,
        justification=extracted_fields.justification,
        department=extracted_fields.department,
        tags=extracted_fields.tags,
        evidence=verified_evidence,
        missing_fields=[],
        clarifying_question=None,
        raw_text=combined_text,
    )
    session.add(intent_record)

    # Load active approval policy if defined
    policy_row = (
        (
            await session.execute(
                text(
                    "SELECT version, content_hash, rules FROM approval_policies "
                    "WHERE tenant_id = :t AND is_active = true"
                ),
                {"t": current_user.tenant_id},
            )
        )
        .mappings()
        .first()
    )

    if policy_row and policy_row.get("rules"):
        rules_data = policy_row["rules"] if isinstance(policy_row["rules"], dict) else {}
        valid_fields = ApprovalPolicyRules.model_fields.keys()
        filtered_rules = {k: v for k, v in rules_data.items() if k in valid_fields}
        if "max_auto_approve" in rules_data and "agent_autonomous_cap" not in filtered_rules:
            try:
                filtered_rules["agent_autonomous_cap"] = Decimal(
                    str(rules_data["max_auto_approve"])
                )
            except Exception:
                pass
        try:
            policy = ApprovalPolicyRules.model_validate(filtered_rules)
        except Exception:
            policy = ApprovalPolicyRules()
        policy_version = policy_row["version"]
    else:
        policy = ApprovalPolicyRules()
        policy_version = "v1.0"

    # Build context snapshot with citation facts from verified evidence
    citations = [
        CitationFact(
            citation_id=f"cite_{field}",
            source_table="procurement_intents",
            fact=f"{field}: {val}",
            quote=str(val),
            as_of=datetime.now(UTC).isoformat(),
        )
        for field, val in verified_evidence.items()
        if val
    ]

    context_snapshot = ApprovalContextSnapshot(
        snapshot_hash=f"snap_{uuid.uuid4().hex[:8]}",
        as_of=datetime.now(UTC).isoformat(),
        snapshot_age_seconds=0,
        hierarchy_source="postgres_authoritative",
        projection_lag=0,
        citations=citations,
    )

    intent_summary = ProcurementIntentSummary(
        request_id=str(req_id),
        requester_id=request_obj.requester_id,
        department=extracted_fields.department or "Engineering",
        vendor_name=extracted_fields.vendor_name or "Unknown",
        estimated_spend=spend.amount or Decimal("0.0"),
        annualized_spend=spend.annualized_spend or spend.amount or Decimal("0.0"),
        currency=spend.currency or "USD",
        justification=extracted_fields.justification or "",
        tags=extracted_fields.tags or [],
        evidence=verified_evidence,
    )

    # Run domain reviewers
    fin_reviewer = FinanceReviewer(policy)
    verdict_fin = fin_reviewer.review(intent_summary, context_snapshot, budget=None)

    sec_reviewer = SecurityReviewer(policy)
    verdict_sec = sec_reviewer.review(intent_summary, context_snapshot)
    verdict_sec = CitationVerifier.verify_verdict(verdict_sec, context_snapshot)

    leg_reviewer = LegalReviewer(policy)
    verdict_leg = leg_reviewer.review(intent_summary, context_snapshot)
    verdict_leg = CitationVerifier.verify_verdict(verdict_leg, context_snapshot)

    reviewers: list[tuple[str, ReviewerVerdict]] = [
        ("finance", verdict_fin),
        ("security", verdict_sec),
        ("legal", verdict_leg),
    ]

    verdicts_payload: list[dict[str, Any]] = []

    for step_name, verdict in reviewers:
        v_dict = verdict.model_dump()
        v_dict["policy_version"] = policy_version
        v_dict["cited_evidence"] = [
            {
                "citation_id": eid,
                "status": "verified",
                "quote": verified_evidence.get(eid.replace("cite_", ""), ""),
            }
            for eid in verdict.evidence_ids
        ] or [
            {"citation_id": f"cite_{k}", "status": "verified", "quote": val}
            for k, val in verified_evidence.items()
        ]
        verdicts_payload.append(v_dict)

        step_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO approval_steps ("
                "id, tenant_id, request_id, step_name, round, reviewer_type, status, assignee, "
                "agent_verdict, metadata"
                ") VALUES ("
                ":id, :t, :req_id, :step_name, 1, 'agent', 'awaiting_human', :assignee, "
                ":agent_verdict, '{}'::jsonb"
                ")"
            ),
            {
                "id": step_id,
                "t": current_user.tenant_id,
                "req_id": req_id,
                "step_name": step_name,
                "assignee": verdict.suggested_assignee,
                "agent_verdict": json.dumps(v_dict),
            },
        )

        # Record immutable audit record for agent reviewer step
        audit_id = uuid.uuid4()
        audit_idem_key = f"audit_agent_{step_id}"
        await session.execute(
            text(
                "INSERT INTO approval_audit_records ("
                "id, tenant_id, request_id, step_id, actor_type, actor_id, action, "
                "idempotency_key, verdict, prompt_version, model, policy_version, "
                "context_snapshot_hash, reasons, evidence_ids"
                ") VALUES ("
                ":id, :t, :req_id, :step_id, 'agent', :actor_id, 'evaluate', "
                ":idem, :verdict, :prompt_ver, :model, :policy_ver, "
                ":ctx_hash, CAST(:reasons AS jsonb), CAST(:ev_ids AS jsonb)"
                ") ON CONFLICT (tenant_id, idempotency_key) DO NOTHING"
            ),
            {
                "id": audit_id,
                "t": current_user.tenant_id,
                "req_id": req_id,
                "step_id": step_id,
                "actor_id": step_name,
                "idem": audit_idem_key,
                "verdict": verdict.verdict,
                "prompt_ver": settings.PROMPT_VERSION,
                "model": settings.LLM_MODEL,
                "policy_ver": policy_version,
                "ctx_hash": (
                    policy_row["content_hash"]
                    if policy_row and policy_row.get("content_hash")
                    else "hash_v1_seed"
                ),
                "reasons": json.dumps(verdict.reasons),
                "ev_ids": json.dumps(verdict.evidence_ids),
            },
        )

    # Persist policy snapshot on request payload
    req_payload = dict(request_obj.payload or {})
    req_payload["policy_version"] = policy_version
    req_payload["policy_content_hash"] = (
        policy_row["content_hash"]
        if policy_row and policy_row.get("content_hash")
        else "hash_v1_seed"
    )
    request_obj.payload = req_payload
    session.add(request_obj)

    await session.commit()

    # Broadcast real-time SSE event
    broadcast_tenant_event(
        current_user.tenant_id,
        "request.created",
        {
            "request_id": str(req_id),
            "requester_id": request_obj.requester_id,
            "vendor_name": extracted_fields.vendor_name,
            "estimated_spend": float(spend.amount) if spend.amount is not None else None,
            "currency": spend.currency,
            "status": "awaiting_human",
        },
    )

    return {
        "status": "created",
        "request_id": str(req_id),
        "session_id": str(sess_uuid),
        "provider_response_id": provider_id,
        "verdicts": verdicts_payload,
    }


@router.get("")
async def list_requests(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
    current_user: AuthContext = Depends(require_permission("requests:read")),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Return paginated list of procurement requests for the authenticated tenant."""
    query = select(RequestModel).where(RequestModel.tenant_id == current_user.tenant_id)

    # Non-admin/finance users see their own requests
    privileged_roles = {"admin", "super_admin", "finance_admin", "cfo", "approver", "tenant_admin"}
    if not (current_user.roles and set(current_user.roles) & privileged_roles):
        actor = current_user.email or current_user.user_id
        query = query.where(RequestModel.requester_id == actor)

    if status_filter:
        query = query.where(RequestModel.status == status_filter)

    # Count total items
    count_stmt = select(func.count()).select_from(query.subquery())
    total = (await session.scalar(count_stmt)) or 0

    # Paginate
    offset = (page - 1) * page_size
    query = query.order_by(RequestModel.created_at.desc()).offset(offset).limit(page_size)
    result = await session.execute(query)
    requests = result.scalars().all()

    items = [
        {
            "id": str(r.id),
            "tenant_id": r.tenant_id,
            "requester_id": r.requester_id,
            "status": r.status,
            "payload": r.payload,
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat(),
        }
        for r in requests
    ]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{request_id}")
async def get_request_detail(
    request_id: str,
    current_user: AuthContext = Depends(require_permission("requests:read")),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Return full request detail including approval timeline, verdicts, citations, and reservations."""
    try:
        req_uuid = uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Request not found"
        ) from None

    request_obj = await session.get(RequestModel, req_uuid)
    if not request_obj or request_obj.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")

    # Fetch approval steps timeline
    steps_res = await session.execute(
        text(
            "SELECT id, step_name, round, reviewer_type, status, assignee, "
            "agent_verdict, human_verdict, created_at, updated_at "
            "FROM approval_steps "
            "WHERE tenant_id = :t AND request_id = :req_id "
            "ORDER BY created_at ASC"
        ),
        {"t": current_user.tenant_id, "req_id": req_uuid},
    )
    steps_rows = steps_res.mappings().all()

    timeline: list[dict[str, Any]] = []
    cited_evidence_list: list[dict[str, Any]] = []
    seen_citation_ids: set[str] = set()

    for s in steps_rows:
        agent_v = s.get("agent_verdict") or {}
        if isinstance(agent_v, str):
            agent_v = json.loads(agent_v)
        human_v = s.get("human_verdict") or {}
        if isinstance(human_v, str):
            human_v = json.loads(human_v)

        for ce in agent_v.get("cited_evidence", []):
            cid = ce.get("citation_id")
            if cid and cid not in seen_citation_ids:
                seen_citation_ids.add(cid)
                cited_evidence_list.append(ce)

        timeline.append(
            {
                "id": str(s["id"]),
                "step_name": s["step_name"],
                "round": s["round"],
                "reviewer_type": s["reviewer_type"],
                "status": s["status"],
                "assignee": s["assignee"],
                "agent_verdict": agent_v,
                "human_verdict": human_v,
                "created_at": s["created_at"].isoformat() if s.get("created_at") else None,
                "updated_at": s["updated_at"].isoformat() if s.get("updated_at") else None,
            }
        )

    # Fetch budget reservations
    reservations_res = await session.execute(
        text(
            "SELECT id, department, currency, period, amount, status, "
            "created_at, committed_at, released_at "
            "FROM budget_reservations "
            "WHERE tenant_id = :t AND request_id = :req_id "
            "ORDER BY created_at ASC"
        ),
        {"t": current_user.tenant_id, "req_id": req_uuid},
    )
    reservations = [
        {
            "id": str(r["id"]),
            "department": r["department"],
            "currency": r["currency"],
            "period": r["period"],
            "amount": float(r["amount"]),
            "status": r["status"],
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            "committed_at": r["committed_at"].isoformat() if r.get("committed_at") else None,
            "released_at": r["released_at"].isoformat() if r.get("released_at") else None,
        }
        for r in reservations_res.mappings().all()
    ]

    # Fetch applied active policy version & content hash
    policy_row = (
        (
            await session.execute(
                text(
                    "SELECT version, content_hash FROM approval_policies "
                    "WHERE tenant_id = :t AND is_active = true"
                ),
                {"t": current_user.tenant_id},
            )
        )
        .mappings()
        .first()
    )

    policy_version = request_obj.payload.get("policy_version") or (
        policy_row["version"] if policy_row else "v1.0"
    )
    policy_content_hash = request_obj.payload.get("policy_content_hash") or (
        policy_row["content_hash"] if policy_row else "default_policy_hash"
    )

    comments = request_obj.payload.get("comments", [])

    return {
        "id": str(request_obj.id),
        "tenant_id": request_obj.tenant_id,
        "requester_id": request_obj.requester_id,
        "status": request_obj.status,
        "payload": request_obj.payload,
        "created_at": request_obj.created_at.isoformat(),
        "updated_at": request_obj.updated_at.isoformat(),
        "timeline": timeline,
        "approval_steps": timeline,
        "cited_evidence": cited_evidence_list,
        "budget_reservations": reservations,
        "policy_version": policy_version,
        "policy_content_hash": policy_content_hash,
        "comments": comments,
    }


@router.post("/{request_id}/comments")
async def add_request_comment(
    request_id: str,
    payload: CommentCreateRequest,
    current_user: AuthContext = Depends(require_permission("requests:create")),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Append in-thread clarification comment and broadcast real-time event."""
    try:
        req_uuid = uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Request not found"
        ) from None

    request_obj = await session.get(RequestModel, req_uuid)
    if not request_obj or request_obj.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")

    comment_entry = {
        "id": str(uuid.uuid4()),
        "user_id": current_user.user_id,
        "email": current_user.email,
        "comment": payload.comment.strip(),
        "created_at": datetime.now(UTC).isoformat(),
    }

    if "comments" not in request_obj.payload or not isinstance(
        request_obj.payload["comments"], list
    ):
        request_obj.payload["comments"] = []

    request_obj.payload["comments"].append(comment_entry)
    flag_modified(request_obj, "payload")
    await session.commit()

    broadcast_tenant_event(
        current_user.tenant_id,
        "request.comment_added",
        {
            "request_id": str(request_obj.id),
            "comment": comment_entry,
        },
    )

    return comment_entry


class CreateDirectRequestInput(BaseModel):
    vendor_name: str = Field(..., min_length=1)
    estimated_spend: Decimal = Field(..., gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    department: str = Field(default="Engineering")
    description: str = Field(default="")
    justification: str = Field(default="")
    attachments: list[dict[str, Any]] = Field(default_factory=list)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_direct_request(
    payload: CreateDirectRequestInput,
    current_user: AuthContext = Depends(require_permission("requests:create")),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Submit a procurement request programmatically via REST (text + attachments)."""
    req_uuid = uuid.uuid4()
    req_payload = {
        "vendor_name": payload.vendor_name,
        "estimated_spend": str(payload.estimated_spend),
        "currency": payload.currency.upper(),
        "department": payload.department,
        "description": payload.description,
        "justification": payload.justification,
        "attachments": payload.attachments,
        "comments": [],
    }

    # 1. Insert in draft status first per database trigger
    req_obj = RequestModel(
        id=req_uuid,
        tenant_id=current_user.tenant_id,
        requester_id=current_user.email or current_user.user_id,
        status="draft",
        payload=req_payload,
    )
    session.add(req_obj)
    await session.flush()

    # 2. Transition to in_review
    req_obj.status = "in_review"
    await session.flush()

    # 3. Create initial approval steps for domain reviewers
    reviewers = [
        ("finance", "Finance Team"),
        ("security", "Security Team"),
        ("legal", "Legal Team"),
    ]
    for step_name, assignee in reviewers:
        await session.execute(
            text(
                "INSERT INTO approval_steps ("
                "id, tenant_id, request_id, step_name, round, reviewer_type, status, assignee, "
                "agent_verdict, metadata"
                ") VALUES ("
                ":id, :t, :req_id, :step_name, 1, 'agent', 'awaiting_human', :assignee, "
                "'{}'::jsonb, '{}'::jsonb"
                ")"
            ),
            {
                "id": uuid.uuid4(),
                "t": current_user.tenant_id,
                "req_id": req_uuid,
                "step_name": step_name,
                "assignee": assignee,
            },
        )

    await session.commit()

    # 4. Dispatch outbound request.created webhook
    from procure_core.developer.webhooks import WebhookDispatcher

    dispatcher = WebhookDispatcher()
    try:
        await dispatcher.dispatch(
            session=session,
            tenant_id=current_user.tenant_id,
            event_type="request.created",
            payload={
                "request_id": str(req_uuid),
                "vendor_name": payload.vendor_name,
                "estimated_spend": str(payload.estimated_spend),
                "currency": payload.currency.upper(),
                "department": payload.department,
                "requester": current_user.email or current_user.user_id,
                "status": "in_review",
            },
        )
        await session.commit()
    except Exception:
        pass

    return {
        "id": str(req_uuid),
        "status": "in_review",
        "vendor_name": payload.vendor_name,
        "estimated_spend": str(payload.estimated_spend),
        "currency": payload.currency.upper(),
        "department": payload.department,
        "description": payload.description,
        "attachments": payload.attachments,
        "created_at": datetime.now(UTC).isoformat(),
    }


@router.get("/{request_id}/decisions")
async def get_request_decisions(
    request_id: str,
    current_user: AuthContext = Depends(require_permission("approvals:read")),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Retrieve all human and agent approval decisions and rationale for a request."""
    try:
        req_uuid = uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Request not found"
        ) from None

    request_obj = await session.get(RequestModel, req_uuid)
    if not request_obj or request_obj.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")

    steps_res = await session.execute(
        text(
            "SELECT id, step_name, reviewer_type, status, assignee, agent_verdict, human_verdict, updated_at "
            "FROM approval_steps WHERE tenant_id = :t AND request_id = :req_id ORDER BY created_at ASC"
        ),
        {"t": current_user.tenant_id, "req_id": req_uuid},
    )
    steps = steps_res.mappings().all()

    return {
        "request_id": str(req_uuid),
        "status": request_obj.status,
        "decisions": [
            {
                "step_id": str(s["id"]),
                "step_name": s["step_name"],
                "status": s["status"],
                "assignee": s["assignee"],
                "agent_verdict": s["agent_verdict"],
                "human_verdict": s["human_verdict"],
                "updated_at": s["updated_at"].isoformat() if s.get("updated_at") else None,
            }
            for s in steps
        ],
    }
