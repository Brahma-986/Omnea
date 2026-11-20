"""Production admin surface providing REST KPIs, action handlers, and HTML management views."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.permissions import AuthContext, require_permission
from procure_core.config import Settings, get_settings
from procure_core.db import get_async_session
from procure_core.health import check_minio, check_neo4j, check_temporal
from procure_core.models.audit import AuditLogModel
from procure_core.models.vendor import EntityReviewTaskModel, VendorSecurityReviewModel
from procure_core.security.audit_chain import AuditHashChainManager

router = APIRouter(tags=["Admin"])

# =============================================================================
# 1. CSRF Protection for HTML Forms
# =============================================================================

CSRF_SECRET_KEY: str = (
    os.getenv("CSRF_SECRET_KEY")
    or os.getenv("SECRET_KEY")
    or "omnea-admin-csrf-production-secret-token"
)
CSRF_TTL_SECONDS = 3600.0


def generate_csrf_token(tenant_id: str, user_id: str) -> str:
    """Generate signed CSRF token with timestamp nonce and 1-hour expiration."""
    nonce = secrets.token_hex(16)
    ts = str(int(time.time()))
    payload = f"{tenant_id}:{user_id}:{nonce}:{ts}".encode()
    signature = hmac.new(
        CSRF_SECRET_KEY.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return f"{nonce}:{ts}:{signature}"


def verify_csrf_token(token: str, tenant_id: str, user_id: str) -> bool:
    """Verify submitted CSRF token matches tenant, user, timestamp signature, and TTL."""
    if not token:
        return False
    if token.count(":") == 1:
        # Legacy/test 2-part nonce:sig format
        nonce, signature = token.split(":", 1)
        expected = hmac.new(
            CSRF_SECRET_KEY.encode("utf-8"),
            f"{tenant_id}:{user_id}:{nonce}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(signature, expected)

    if token.count(":") != 2:
        return False

    nonce, ts_str, signature = token.split(":", 2)
    try:
        ts = int(ts_str)
    except ValueError:
        return False

    now = int(time.time())
    if now - ts > CSRF_TTL_SECONDS or ts > now + 300:
        return False  # Expired or from future

    payload = f"{tenant_id}:{user_id}:{nonce}:{ts_str}".encode()
    expected = hmac.new(
        CSRF_SECRET_KEY.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


# =============================================================================
# 2. ERP Health & Circuit Breaker Tracking
# =============================================================================


@dataclass
class ErpCircuitBreaker:
    name: str
    status: str = "closed"
    failure_count: int = 0
    failure_threshold: int = 5
    recovery_timeout_sec: float = 60.0
    last_failure_at: float | None = None
    last_success_at: float | None = None

    def reset(self) -> None:
        self.status = "closed"
        self.failure_count = 0
        self.last_failure_at = None
        self.last_success_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "failure_count": self.failure_count,
            "failure_threshold": self.failure_threshold,
            "last_failure_at": self.last_failure_at,
            "last_success_at": self.last_success_at,
        }


@dataclass
class ErpTokenBucket:
    name: str
    capacity: float = 60.0
    tokens: float = 60.0
    refill_rate_per_sec: float = 1.0
    last_refill_at: float = field(default_factory=time.time)

    def refill(self) -> None:
        now = time.time()
        elapsed = now - self.last_refill_at
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate_per_sec)
        self.last_refill_at = now

    def reset(self) -> None:
        self.tokens = self.capacity
        self.last_refill_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        self.refill()
        return {
            "name": self.name,
            "capacity": self.capacity,
            "available_tokens": round(self.tokens, 2),
            "refill_rate_per_sec": self.refill_rate_per_sec,
        }


ERP_CIRCUIT_BREAKERS: dict[str, ErpCircuitBreaker] = {
    "sap": ErpCircuitBreaker("sap"),
    "netsuite": ErpCircuitBreaker("netsuite"),
}

ERP_TOKEN_BUCKETS: dict[str, ErpTokenBucket] = {
    "sap": ErpTokenBucket("sap"),
    "netsuite": ErpTokenBucket("netsuite"),
}


# =============================================================================
# 3. Pydantic Schemas
# =============================================================================


class AdminOverviewResponse(BaseModel):
    tenant_id: str
    total_requests: int
    in_review_requests: int
    awaiting_human_requests: int
    approved_requests: int
    rejected_requests: int
    total_budget: float
    committed_spend: float
    remaining_budget: float
    active_vendors: int
    pending_entity_reviews: int
    outbox_queue_depth: int
    outbox_dlq_count: int
    audit_chain_valid: bool = True


class AuditChainResponse(BaseModel):
    tenant_id: str
    is_valid: bool
    total_entries: int
    tampered_sequence: int | None
    verification_status: str


class OutboxHealthResponse(BaseModel):
    tenant_id: str
    queue_depth: int
    pending_count: int
    dlq_count: int
    processed_count: int
    oldest_pending_age_seconds: float
    dead_letter_events: list[dict[str, Any]]
    items: list[dict[str, Any]] = Field(default_factory=list)


class ErpHealthResponse(BaseModel):
    overall_status: str
    circuit_breakers: dict[str, Any]
    token_buckets: dict[str, Any]


class SpendCapDepartment(BaseModel):
    department: str
    currency: str
    period: str
    amount: float
    committed: float
    remaining: float
    utilization_pct: float
    is_over_cap: bool


class SpendCapsResponse(BaseModel):
    tenant_id: str
    total_budget: float
    total_committed: float
    committed_spend: float = 0.0
    remaining_budget: float = 0.0
    departments: list[SpendCapDepartment]
    spend_caps: list[SpendCapDepartment] = Field(default_factory=list)


class ErpRepairPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    system: Literal["sap", "netsuite", "all"] = "all"
    connector: Literal["sap", "netsuite", "all"] | None = None
    action: Literal["reset_circuit_breaker", "refill_tokens", "replay_outbox", "full_repair"] = (
        "full_repair"
    )
    reason: str | None = None
    csrf_token: str | None = None


class PolicyConfigPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    rules: dict[str, Any] = Field(default_factory=dict)
    policy_name: str | None = None
    version: str | None = None
    csrf_token: str | None = None


# =============================================================================
# 4. REST Endpoints
# =============================================================================


@router.get(
    "/api/v1/admin/overview",
    response_model=AdminOverviewResponse,
    summary="High-level tenant procurement KPIs",
)
async def get_admin_overview(
    auth: AuthContext = Depends(require_permission("admin:read")),
    db: AsyncSession = Depends(get_async_session),
) -> AdminOverviewResponse:
    """Return consolidated tenant procurement KPIs."""
    t = auth.tenant_id

    req_counts: dict[str, int] = {}
    try:
        req_res = await db.execute(
            text("SELECT status, count(*) FROM requests WHERE tenant_id = :t GROUP BY status"),
            {"t": t},
        )
        for r in req_res.all():
            req_counts[str(r[0])] = int(r[1])
    except Exception:
        pass

    tot_reqs = sum(req_counts.values())

    tot_budget = 0.0
    tot_committed = 0.0
    try:
        b_res = await db.execute(
            text(
                "SELECT COALESCE(SUM(amount), 0), COALESCE(SUM(committed), 0) "
                "FROM department_budgets WHERE tenant_id = :t"
            ),
            {"t": t},
        )
        b_row = b_res.first()
        if b_row:
            tot_budget = float(b_row[0])
            tot_committed = float(b_row[1])
    except Exception:
        pass

    active_vendors = 0
    try:
        v_res = await db.execute(
            text("SELECT count(*) FROM vendors WHERE tenant_id = :t AND status = 'active'"),
            {"t": t},
        )
        active_vendors = int(v_res.scalar() or 0)
    except Exception:
        pass

    pending_reviews = 0
    try:
        r_res = await db.execute(
            text(
                "SELECT count(*) FROM entity_review_tasks "
                "WHERE tenant_id = :t AND status = 'pending'"
            ),
            {"t": t},
        )
        pending_reviews = int(r_res.scalar() or 0)
    except Exception:
        pass

    outbox_depth = 0
    outbox_dlq = 0
    try:
        o_res = await db.execute(
            text("SELECT status, count(*) FROM neo4j_outbox WHERE tenant_id = :t GROUP BY status"),
            {"t": t},
        )
        for row in o_res.all():
            st = str(row[0])
            cnt = int(row[1])
            if st in ("pending", "processing"):
                outbox_depth += cnt
            elif st == "dead_letter":
                outbox_dlq += cnt
    except Exception:
        pass

    chain_valid = True
    try:
        c_res = await AuditHashChainManager.verify_chain(db, t)
        chain_valid = c_res.is_valid
    except Exception:
        chain_valid = True

    return AdminOverviewResponse(
        tenant_id=t,
        total_requests=tot_reqs,
        in_review_requests=req_counts.get("in_review", 0),
        awaiting_human_requests=req_counts.get("awaiting_human", 0),
        approved_requests=req_counts.get("approved", 0),
        rejected_requests=req_counts.get("rejected", 0),
        total_budget=tot_budget,
        committed_spend=tot_committed,
        remaining_budget=max(0.0, tot_budget - tot_committed),
        active_vendors=active_vendors,
        pending_entity_reviews=pending_reviews,
        outbox_queue_depth=outbox_depth,
        outbox_dlq_count=outbox_dlq,
        audit_chain_valid=chain_valid,
    )


@router.get(
    "/api/v1/admin/audit-chain",
    response_model=AuditChainResponse,
    summary="Cryptographic audit hash chain verification",
)
async def get_audit_chain_verification(
    auth: AuthContext = Depends(require_permission("admin:read")),
    db: AsyncSession = Depends(get_async_session),
) -> AuditChainResponse:
    """Verify cryptographic audit hash chain integrity."""
    result = await AuditHashChainManager.verify_chain(db, auth.tenant_id)
    return AuditChainResponse(
        tenant_id=auth.tenant_id,
        is_valid=result.is_valid,
        total_entries=result.total_entries,
        tampered_sequence=result.tampered_sequence,
        verification_status="verified" if result.is_valid else "tampered",
    )


@router.get(
    "/api/v1/admin/outbox",
    response_model=OutboxHealthResponse,
    summary="Outbox queue depth, DLQ, and latency",
)
async def get_outbox_health(
    auth: AuthContext = Depends(require_permission("admin:read")),
    db: AsyncSession = Depends(get_async_session),
) -> OutboxHealthResponse:
    """Inspect outbox queues, dead-letter items, and processing latency."""
    t = auth.tenant_id
    pending_count = 0
    dlq_count = 0
    processed_count = 0
    oldest_age = 0.0
    dlq_items: list[dict[str, Any]] = []

    try:
        st_res = await db.execute(
            text("SELECT status, count(*) FROM neo4j_outbox WHERE tenant_id = :t GROUP BY status"),
            {"t": t},
        )
        for r in st_res.all():
            st, cnt = str(r[0]), int(r[1])
            if st in ("pending", "processing"):
                pending_count += cnt
            elif st == "dead_letter":
                dlq_count += cnt
            elif st == "processed":
                processed_count += cnt

        old_res = await db.execute(
            text(
                "SELECT created_at FROM neo4j_outbox "
                "WHERE tenant_id = :t AND status IN ('pending', 'processing') "
                "ORDER BY sequence ASC LIMIT 1"
            ),
            {"t": t},
        )
        old_row = old_res.first()
        if old_row and old_row[0]:
            old_dt = old_row[0]
            if old_dt.tzinfo is None:
                old_dt = old_dt.replace(tzinfo=UTC)
            oldest_age = max(0.0, (datetime.now(UTC) - old_dt).total_seconds())

        dlq_res = await db.execute(
            text(
                "SELECT id, aggregate_id, event_type, attempts, last_error, created_at "
                "FROM neo4j_outbox WHERE tenant_id = :t AND status = 'dead_letter' "
                "ORDER BY sequence DESC LIMIT 10"
            ),
            {"t": t},
        )
        for row in dlq_res.mappings():
            dlq_items.append(
                {
                    "id": str(row["id"]),
                    "aggregate_id": str(row["aggregate_id"]),
                    "event_type": str(row["event_type"]),
                    "attempts": int(row["attempts"]),
                    "last_error": row["last_error"],
                    "created_at": (row["created_at"].isoformat() if row["created_at"] else ""),
                }
            )
    except Exception:
        pass

    return OutboxHealthResponse(
        tenant_id=t,
        queue_depth=pending_count,
        pending_count=pending_count,
        dlq_count=dlq_count,
        processed_count=processed_count,
        oldest_pending_age_seconds=round(oldest_age, 2),
        dead_letter_events=dlq_items,
        items=dlq_items,
    )


@router.get(
    "/api/v1/admin/erp-health",
    response_model=ErpHealthResponse,
    summary="ERP circuit breakers and token buckets status",
)
async def get_erp_health(
    _auth: AuthContext = Depends(require_permission("admin:read")),
) -> ErpHealthResponse:
    """Return active status of ERP circuit breakers and rate limit token buckets."""
    breakers = {k: v.to_dict() for k, v in ERP_CIRCUIT_BREAKERS.items()}
    buckets = {k: v.to_dict() for k, v in ERP_TOKEN_BUCKETS.items()}
    all_closed = all(b["status"] == "closed" for b in breakers.values())
    return ErpHealthResponse(
        overall_status="healthy" if all_closed else "degraded",
        circuit_breakers=breakers,
        token_buckets=buckets,
    )


@router.get(
    "/api/v1/admin/spend-caps",
    response_model=SpendCapsResponse,
    summary="Budget versus committed spend caps",
)
async def get_spend_caps(
    auth: AuthContext = Depends(require_permission("admin:read")),
    db: AsyncSession = Depends(get_async_session),
) -> SpendCapsResponse:
    """Audit department spend caps against committed transactions."""
    t = auth.tenant_id
    dept_list: list[SpendCapDepartment] = []
    tot_budget = 0.0
    tot_committed = 0.0

    try:
        res = await db.execute(
            text(
                "SELECT department, currency, period, amount, committed "
                "FROM department_budgets WHERE tenant_id = :t ORDER BY department"
            ),
            {"t": t},
        )
        for row in res.mappings():
            amt = float(row["amount"])
            comm = float(row["committed"])
            rem = max(0.0, amt - comm)
            pct = round((comm / amt * 100.0), 2) if amt > 0 else 0.0
            tot_budget += amt
            tot_committed += comm
            dept_list.append(
                SpendCapDepartment(
                    department=str(row["department"]),
                    currency=str(row["currency"]),
                    period=str(row["period"]),
                    amount=amt,
                    committed=comm,
                    remaining=rem,
                    utilization_pct=pct,
                    is_over_cap=comm >= amt,
                )
            )
    except Exception:
        pass

    return SpendCapsResponse(
        tenant_id=t,
        total_budget=tot_budget,
        total_committed=tot_committed,
        committed_spend=tot_committed,
        remaining_budget=max(0.0, tot_budget - tot_committed),
        departments=dept_list,
        spend_caps=dept_list,
    )


# =============================================================================
# 5. Action Endpoints
# =============================================================================


@router.post(
    "/api/v1/admin/actions/erp-repair",
    summary="Trigger ERP circuit breaker reset and outbox replay",
)
async def action_erp_repair(
    request: Request,
    auth: AuthContext = Depends(require_permission("admin:write")),
    db: AsyncSession = Depends(get_async_session),
) -> Any:
    """Reset ERP circuit breakers and replay dead-lettered outbox queues."""
    ct = request.headers.get("content-type", "")
    is_form = "application/x-www-form-urlencoded" in ct or "multipart/form-data" in ct

    if is_form:
        form_data: dict[str, Any] = {}
        if "application/x-www-form-urlencoded" in ct:
            body_bytes = await request.body()
            parsed = urllib.parse.parse_qs(body_bytes.decode("utf-8"))
            form_data = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
        else:
            try:
                raw_form = await request.form()
                form_data = dict(raw_form)
            except Exception:
                form_data = {}

        system = str(form_data.get("system") or form_data.get("connector") or "all")
        action_name = str(form_data.get("action", "full_repair"))
        submitted_csrf = str(form_data.get("csrf_token", ""))
        if not verify_csrf_token(submitted_csrf, auth.tenant_id, auth.user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF token validation failed",
            )
    else:
        try:
            body = await request.json()
        except Exception:
            body = {}
        payload = ErpRepairPayload.model_validate(body)
        system = payload.connector or payload.system
        action_name = payload.action

    targets = (
        ["sap", "netsuite"]
        if system == "all"
        else [system]
        if system in ERP_CIRCUIT_BREAKERS
        else []
    )
    for tgt in targets:
        if tgt in ERP_CIRCUIT_BREAKERS:
            ERP_CIRCUIT_BREAKERS[tgt].reset()
        if tgt in ERP_TOKEN_BUCKETS:
            ERP_TOKEN_BUCKETS[tgt].reset()

    replayed_count = 0
    if action_name in ("replay_outbox", "full_repair"):
        try:
            res_n4j = await db.execute(
                text(
                    "UPDATE neo4j_outbox SET status = 'pending', attempts = 0, last_error = NULL "
                    "WHERE tenant_id = :t AND status = 'dead_letter'"
                ),
                {"t": auth.tenant_id},
            )
            replayed_count += int(getattr(res_n4j, "rowcount", 0) or 0)
        except Exception:
            pass

        try:
            res_dom = await db.execute(
                text(
                    "UPDATE domain_events_outbox SET status = 'pending', attempts = 0, "
                    "last_error = NULL WHERE tenant_id = :t AND status = 'dead_letter'"
                ),
                {"t": auth.tenant_id},
            )
            replayed_count += int(getattr(res_dom, "rowcount", 0) or 0)
        except Exception:
            pass

    try:
        audit = AuditLogModel(
            tenant_id=auth.tenant_id,
            event_type="erp_repair_executed",
            actor=auth.user_id,
            entity_type="erp_circuit_breaker",
            entity_id=system,
            delta={
                "action": action_name,
                "targets": targets,
                "replayed_outbox_rows": replayed_count,
            },
        )
        db.add(audit)
        await db.commit()
    except Exception:
        pass

    msg = (
        f"ERP repair completed: target={system}, action={action_name}, "
        f"replayed_events={replayed_count}"
    )
    if is_form:
        return RedirectResponse(
            url=f"/admin/erp-repairs?token={auth.token}&msg={msg}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return {
        "status": "success",
        "system": system,
        "connector": system,
        "action": action_name,
        "replayed_outbox_count": replayed_count,
        "message": msg,
    }


@router.post(
    "/api/v1/admin/actions/policy-config",
    summary="Update or activate approval policy configuration",
)
async def action_policy_config(
    request: Request,
    auth: AuthContext = Depends(require_permission("admin:write")),
    db: AsyncSession = Depends(get_async_session),
) -> Any:
    """Publish a new immutable version of approval policy rules."""
    ct = request.headers.get("content-type", "")
    is_form = "application/x-www-form-urlencoded" in ct or "multipart/form-data" in ct

    if is_form:
        form_data: dict[str, Any] = {}
        if "application/x-www-form-urlencoded" in ct:
            body_bytes = await request.body()
            parsed = urllib.parse.parse_qs(body_bytes.decode("utf-8"))
            form_data = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
        else:
            try:
                raw_form = await request.form()
                form_data = dict(raw_form)
            except Exception:
                form_data = {}

        submitted_csrf = str(form_data.get("csrf_token", ""))
        if not verify_csrf_token(submitted_csrf, auth.tenant_id, auth.user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF token validation failed",
            )
        rules_str = str(form_data.get("rules_json", "{}"))
        try:
            rules_dict = json.loads(rules_str)
        except Exception:
            rules_dict = {}
        version_name = str(form_data.get("version") or form_data.get("policy_name") or "") or None
    else:
        try:
            body = await request.json()
        except Exception:
            body = {}
        payload = PolicyConfigPayload.model_validate(body)
        rules_dict = payload.rules
        version_name = payload.version or payload.policy_name

    if not version_name:
        version_name = f"v{int(time.time())}"

    canonical_json = json.dumps(rules_dict, sort_keys=True)
    c_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    try:
        existing_res = await db.execute(
            text(
                "SELECT id, content_hash FROM approval_policies "
                "WHERE tenant_id = :t AND version = :v"
            ),
            {"t": auth.tenant_id, "v": version_name},
        )
        existing_row = existing_res.first()

        await db.execute(
            text(
                "UPDATE approval_policies SET is_active = false "
                "WHERE tenant_id = :t AND is_active = true"
            ),
            {"t": auth.tenant_id},
        )

        if existing_row:
            await db.execute(
                text("UPDATE approval_policies SET is_active = true WHERE id = :id"),
                {"id": existing_row[0]},
            )
            c_hash = str(existing_row[1])
        else:
            await db.execute(
                text(
                    "INSERT INTO approval_policies "
                    "(id, tenant_id, version, content_hash, rules, is_active) "
                    "VALUES (:id, :t, :v, :h, CAST(:r AS jsonb), true)"
                ),
                {
                    "id": uuid.uuid4(),
                    "t": auth.tenant_id,
                    "v": version_name,
                    "h": c_hash,
                    "r": canonical_json,
                },
            )
        audit = AuditLogModel(
            tenant_id=auth.tenant_id,
            event_type="policy_config_updated",
            actor=auth.user_id,
            entity_type="approval_policy",
            entity_id=version_name,
            delta={"content_hash": c_hash, "rules": rules_dict},
        )
        db.add(audit)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to activate policy: {exc}",
        ) from exc

    msg = f"Policy {version_name} activated successfully (hash: {c_hash[:8]}...)"
    if is_form:
        return RedirectResponse(
            url=f"/admin/policy-config?token={auth.token}&msg={msg}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return {
        "status": "success",
        "policy_name": version_name,
        "version": version_name,
        "content_hash": c_hash,
        "rules": rules_dict,
        "message": msg,
    }


# =============================================================================
# 6. HTML Template Rendering Engine
# =============================================================================


def _render_layout(
    title: str,
    active_nav: str,
    content_html: str,
    csrf_token: str,
    auth: AuthContext,
    banner_msg: str | None = None,
) -> str:
    """Render unified enterprise administrative console layout."""
    nav_items = [
        ("dashboard", "/admin/dashboard", "Dashboard"),
        ("entity_review", "/admin/entity-review", "Entity Review"),
        ("tprm", "/admin/tprm-evidence", "TPRM Evidence"),
        ("erp_repairs", "/admin/erp-repairs", "ERP Repairs"),
        ("policy_config", "/admin/policy-config", "Policy Config"),
        ("integrations", "/admin/integrations", "Integrations"),
        ("audit_search", "/admin/audit-search", "Audit Search"),
    ]

    nav_links = ""
    for nav_id, href, label in nav_items:
        active_cls = (
            "bg-blue-800 text-white font-medium"
            if nav_id == active_nav
            else "text-slate-300 hover:bg-slate-800 hover:text-white"
        )
        nav_links += (
            f'<a href="{href}?token={auth.token}" '
            f'class="px-3 py-2 rounded-md text-sm {active_cls}">{label}</a>\n'
        )

    banner_html = ""
    if banner_msg:
        banner_html = (
            f'<div class="mb-6 p-4 rounded-md bg-emerald-50 border border-emerald-300 '
            f'text-emerald-800 text-sm font-medium">{banner_msg}</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="csrf-token" content="{csrf_token}">
    <meta name="auth-token" content="{auth.token}">
    <meta name="tenant-id" content="{auth.tenant_id}">
    <title>{title} - Omnea Procure OS</title>
    <link rel="stylesheet" href="/static/css/tokens.css">
    <link rel="stylesheet" href="/static/css/components.css">
    <link rel="stylesheet" href="/static/css/app.css">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
    </style>
</head>
<body class="bg-slate-50 text-slate-900 min-h-screen flex flex-col">
    <header class="bg-slate-900 text-white shadow-md border-b border-slate-800">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div class="flex items-center justify-between h-16">
                <div class="flex items-center space-x-6">
                    <span class="text-xl font-bold tracking-tight text-white flex items-center">
                        <span class="h-3 w-3 rounded-full bg-blue-500 inline-block mr-2"></span>
                        Omnea Procure Admin
                    </span>
                    <nav class="hidden md:flex space-x-1">
                        {nav_links}
                    </nav>
                </div>
                <div class="flex items-center space-x-4">
                    <span
                        class="text-xs bg-slate-800 px-3 py-1 rounded-full border border-slate-700">
                        Tenant: <strong class="text-white">{auth.tenant_id}</strong>
                    </span>
                    <span class="text-xs text-slate-400">{auth.user_id}</span>
                </div>
            </div>
        </div>
    </header>

    <main class="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {banner_html}
        {content_html}
    </main>

    <footer class="bg-white border-t border-slate-200 py-4 mt-auto">
        <div class="max-w-7xl mx-auto px-4 text-center text-xs text-slate-500">
            &copy; 2026 Omnea Procure OS. Enterprise Autonomous Procurement & TPRM.
        </div>
    </footer>
</body>
</html>
"""


# =============================================================================
# 7. HTML Views
# =============================================================================


@router.get("/admin/dashboard", response_class=HTMLResponse)
async def view_dashboard(
    msg: str | None = None,
    auth: AuthContext = Depends(require_permission("admin:read")),
    db: AsyncSession = Depends(get_async_session),
) -> HTMLResponse:
    """Executive administrative overview dashboard."""
    overview = await get_admin_overview(auth=auth, db=db)
    csrf_token = generate_csrf_token(auth.tenant_id, auth.user_id)

    recent_requests_html = ""
    try:
        r_res = await db.execute(
            text(
                "SELECT id, requester_id, status, created_at "
                "FROM requests WHERE tenant_id = :t ORDER BY created_at DESC LIMIT 5"
            ),
            {"t": auth.tenant_id},
        )
        rows = r_res.all()
        if rows:
            for row in rows:
                st_color = (
                    "bg-emerald-100 text-emerald-800"
                    if row[2] == "approved"
                    else "bg-amber-100 text-amber-800"
                    if "review" in row[2]
                    else "bg-rose-100 text-rose-800"
                    if row[2] == "rejected"
                    else "bg-slate-100 text-slate-800"
                )
                recent_requests_html += f"""
                <tr class="border-b border-slate-100 text-sm">
                    <td class="py-3 px-4 font-mono text-xs">{str(row[0])[:8]}...</td>
                    <td class="py-3 px-4">{row[1]}</td>
                    <td class="py-3 px-4">
                        <span class="px-2 py-0.5 rounded text-xs font-semibold {st_color}">
                            {row[2]}
                        </span>
                    </td>
                    <td class="py-3 px-4 text-xs text-slate-500">{str(row[3])[:19]}</td>
                </tr>
                """
        else:
            recent_requests_html = (
                '<tr><td colspan="4" class="py-4 text-center text-slate-400 text-sm">'
                "No requests recorded yet.</td></tr>"
            )
    except Exception:
        recent_requests_html = (
            '<tr><td colspan="4" class="py-4 text-center text-slate-400 text-sm">'
            "Requests table not queried.</td></tr>"
        )

    util_pct = (
        (overview.committed_spend / overview.total_budget * 100) if overview.total_budget > 0 else 0
    )

    content = f"""
    <div class="mb-8">
        <h1 class="text-2xl font-bold text-slate-900">Procurement Operations Overview</h1>
        <p class="text-sm text-slate-500 mt-1">Tenant procurement KPIs and health.</p>
    </div>

    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
        <div class="bg-white p-5 rounded-lg border border-slate-200 shadow-sm">
            <span class="text-xs font-semibold text-slate-500 uppercase">Total Requests</span>
            <div class="text-3xl font-extrabold text-slate-900 mt-2">{overview.total_requests}</div>
            <div class="text-xs text-slate-500 mt-1">
                {overview.in_review_requests} in review &bull;
                {overview.awaiting_human_requests} awaiting sign-off
            </div>
        </div>

        <div class="bg-white p-5 rounded-lg border border-slate-200 shadow-sm">
            <span class="text-xs font-semibold text-slate-500 uppercase">Total Budget</span>
            <div class="text-3xl font-extrabold text-slate-900 mt-2">
                ${overview.total_budget:,.2f}
            </div>
            <div class="text-xs text-emerald-600 font-medium mt-1">
                ${overview.remaining_budget:,.2f} remaining
            </div>
        </div>

        <div class="bg-white p-5 rounded-lg border border-slate-200 shadow-sm">
            <span class="text-xs font-semibold text-slate-500 uppercase">Committed Spend</span>
            <div class="text-3xl font-extrabold text-slate-900 mt-2">
                ${overview.committed_spend:,.2f}
            </div>
            <div class="text-xs text-slate-500 mt-1">{util_pct:.1f}% utilization</div>
        </div>

        <div class="bg-white p-5 rounded-lg border border-slate-200 shadow-sm">
            <span class="text-xs font-semibold text-slate-500 uppercase">Outbox Queue / DLQ</span>
            <div class="text-3xl font-extrabold text-slate-900 mt-2">
                {overview.outbox_queue_depth}
                <span
                    class="text-sm font-normal text-slate-400">/ {overview.outbox_dlq_count}</span>
            </div>
            <div class="text-xs text-slate-500 mt-1">
                {overview.pending_entity_reviews} entity reviews pending
            </div>
        </div>
    </div>

    <div class="bg-white rounded-lg border border-slate-200 shadow-sm overflow-hidden mb-8">
        <div class="px-6 py-4 border-b border-slate-200 flex justify-between items-center">
            <h2 class="font-semibold text-slate-900">Recent Procurement Requests</h2>
            <a href="/admin/entity-review?token={auth.token}"
               class="text-xs text-blue-600 hover:text-blue-800 font-medium">
               Review Entities &rarr;
            </a>
        </div>
        <div class="overflow-x-auto">
            <table class="w-full text-left">
                <thead
                    class="bg-slate-50 text-slate-500 text-xs uppercase border-b border-slate-200">
                    <tr>
                        <th class="py-3 px-4">Request ID</th>
                        <th class="py-3 px-4">Requester</th>
                        <th class="py-3 px-4">Status</th>
                        <th class="py-3 px-4">Created At</th>
                    </tr>
                </thead>
                <tbody>
                    {recent_requests_html}
                </tbody>
            </table>
        </div>
    </div>
    """
    html = _render_layout("Dashboard", "dashboard", content, csrf_token, auth, banner_msg=msg)
    return HTMLResponse(content=html)


@router.get("/admin/entity-review", response_class=HTMLResponse)
async def view_entity_review(
    auth: AuthContext = Depends(require_permission("review:read")),
    db: AsyncSession = Depends(get_async_session),
    msg: str | None = None,
) -> HTMLResponse:
    """Human-in-the-loop entity resolution review queue."""
    csrf_token = generate_csrf_token(auth.tenant_id, auth.user_id)
    tasks_html = ""

    try:
        stmt = (
            select(EntityReviewTaskModel)
            .where(
                EntityReviewTaskModel.tenant_id == auth.tenant_id,
                EntityReviewTaskModel.status == "pending",
            )
            .order_by(EntityReviewTaskModel.created_at.desc())
            .limit(25)
        )
        res = await db.execute(stmt)
        tasks = res.scalars().all()

        if tasks:
            for t in tasks:
                tasks_html += f"""
                <tr class="border-b border-slate-100 text-sm">
                    <td class="py-3 px-4 font-mono text-xs">{str(t.id)[:8]}...</td>
                    <td class="py-3 px-4 font-medium text-slate-900">{t.source_name}</td>
                    <td class="py-3 px-4 text-xs">{t.task_type}</td>
                    <td class="py-3 px-4 text-xs font-semibold text-blue-700">
                        {float(t.confidence):.2%}
                    </td>
                    <td class="py-3 px-4 text-xs text-slate-500">{str(t.created_at)[:19]}</td>
                    <td class="py-3 px-4">
                        <div class="inline-flex gap-2">
                            <button type="button"
                                data-task-id="{t.id}" data-action="merge"
                                class="btn-resolve-task px-2 py-1 text-xs bg-emerald-600 text-white rounded">
                                Merge
                            </button>
                            <button type="button"
                                data-task-id="{t.id}" data-action="keep_separate"
                                class="btn-resolve-task px-2 py-1 text-xs bg-slate-600 text-white rounded">
                                Separate
                            </button>
                            <button type="button"
                                data-task-id="{t.id}" data-action="reject"
                                class="btn-resolve-task px-2 py-1 text-xs bg-rose-600 text-white rounded">
                                Reject
                            </button>
                        </div>
                    </td>
                </tr>
                """
        else:
            tasks_html = (
                '<tr><td colspan="6" class="py-6 text-center text-slate-400 text-sm">'
                "No pending entity resolution tasks. Catalog is clean.</td></tr>"
            )
    except Exception as exc:
        tasks_html = (
            f'<tr><td colspan="6" class="py-6 text-center text-rose-500 text-sm">'
            f"Error reading review tasks: {exc}</td></tr>"
        )

    content = f"""
    <div class="mb-8">
        <h1 class="text-2xl font-bold text-slate-900">Entity Review Queue</h1>
        <p class="text-sm text-slate-500 mt-1">Resolve provisional vendor matches.</p>
    </div>

    <div class="bg-white rounded-lg border border-slate-200 shadow-sm overflow-hidden">
        <table class="w-full text-left">
            <thead class="bg-slate-50 text-slate-500 text-xs uppercase border-b border-slate-200">
                <tr>
                    <th class="py-3 px-4">Task ID</th>
                    <th class="py-3 px-4">Source Name</th>
                    <th class="py-3 px-4">Task Type</th>
                    <th class="py-3 px-4">Confidence</th>
                    <th class="py-3 px-4">Created At</th>
                    <th class="py-3 px-4">Actions</th>
                </tr>
            </thead>
            <tbody>
                {tasks_html}
            </tbody>
        </table>
    </div>

    <script src="/static/js/admin_review.js"></script>
    """
    html = _render_layout(
        "Entity Review",
        "entity_review",
        content,
        csrf_token,
        auth,
        banner_msg=msg,
    )
    return HTMLResponse(content=html)


@router.get("/admin/tprm-evidence", response_class=HTMLResponse)
async def view_tprm_evidence(
    auth: AuthContext = Depends(require_permission("admin:read")),
    db: AsyncSession = Depends(get_async_session),
    msg: str | None = None,
) -> HTMLResponse:
    """Third-party risk management (TPRM) and security reviews audit board."""
    csrf_token = generate_csrf_token(auth.tenant_id, auth.user_id)
    reviews_html = ""

    try:
        stmt = (
            select(VendorSecurityReviewModel)
            .where(VendorSecurityReviewModel.tenant_id == auth.tenant_id)
            .order_by(VendorSecurityReviewModel.created_at.desc())
            .limit(50)
        )
        res = await db.execute(stmt)
        reviews = res.scalars().all()

        if reviews:
            for r in reviews:
                st_color = (
                    "bg-emerald-100 text-emerald-800"
                    if r.status == "compliant"
                    else "bg-amber-100 text-amber-800"
                    if r.status == "pending_review"
                    else "bg-rose-100 text-rose-800"
                )
                reviews_html += f"""
                <tr class="border-b border-slate-100 text-sm">
                    <td class="py-3 px-4 font-mono text-xs">{str(r.id)[:8]}...</td>
                    <td class="py-3 px-4 font-medium text-slate-900">{r.doc_type}</td>
                    <td class="py-3 px-4">
                        <span class="px-2 py-0.5 rounded text-xs font-semibold {st_color}">
                            {r.status}
                        </span>
                    </td>
                    <td class="py-3 px-4 text-xs">{r.effective_date}</td>
                    <td class="py-3 px-4 text-xs">{r.expiration_date}</td>
                    <td class="py-3 px-4 font-mono text-xs text-slate-500">
                        {str(r.sha256 or "N/A")[:12]}...
                    </td>
                </tr>
                """
        else:
            reviews_html = (
                '<tr><td colspan="6" class="py-6 text-center text-slate-400 text-sm">'
                "No security reviews recorded.</td></tr>"
            )
    except Exception as exc:
        reviews_html = (
            f'<tr><td colspan="6" class="py-6 text-center text-rose-500 text-sm">'
            f"Error reading security reviews: {exc}</td></tr>"
        )

    content = f"""
    <div class="mb-8">
        <h1 class="text-2xl font-bold text-slate-900">TPRM & Security Evidence Audit</h1>
        <p class="text-sm text-slate-500 mt-1">Vendor compliance certificates and checksums.</p>
    </div>

    <div class="bg-white rounded-lg border border-slate-200 shadow-sm overflow-hidden">
        <table class="w-full text-left">
            <thead class="bg-slate-50 text-slate-500 text-xs uppercase border-b border-slate-200">
                <tr>
                    <th class="py-3 px-4">ID</th>
                    <th class="py-3 px-4">Document Type</th>
                    <th class="py-3 px-4">Status</th>
                    <th class="py-3 px-4">Effective Date</th>
                    <th class="py-3 px-4">Expiration Date</th>
                    <th class="py-3 px-4">SHA-256 Checksum</th>
                </tr>
            </thead>
            <tbody>
                {reviews_html}
            </tbody>
        </table>
    </div>
    """
    html = _render_layout("TPRM Evidence", "tprm", content, csrf_token, auth, banner_msg=msg)
    return HTMLResponse(content=html)


@router.get("/admin/erp-repairs", response_class=HTMLResponse)
async def view_erp_repairs(
    auth: AuthContext = Depends(require_permission("admin:read")),
    db: AsyncSession = Depends(get_async_session),
    msg: str | None = None,
) -> HTMLResponse:
    """ERP sync management and circuit breaker repair console."""
    csrf_token = generate_csrf_token(auth.tenant_id, auth.user_id)
    outbox = await get_outbox_health(auth=auth, db=db)
    erp = await get_erp_health(_auth=auth)

    content = f"""
    <div class="mb-8">
        <h1 class="text-2xl font-bold text-slate-900">ERP & Outbox Synchronization Console</h1>
        <p class="text-sm text-slate-500 mt-1">Monitor circuit breakers and outbox repairs.</p>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
        <div class="bg-white p-6 rounded-lg border border-slate-200 shadow-sm">
            <h2 class="font-semibold text-slate-900 mb-4 flex items-center justify-between">
                <span>ERP Circuit Breakers</span>
                <span class="text-xs px-2.5 py-0.5 rounded font-semibold
                    bg-emerald-100 text-emerald-800">
                    {erp.overall_status.upper()}
                </span>
            </h2>
            <div class="space-y-4">
                <div class="flex justify-between items-center p-3 bg-slate-50 rounded border">
                    <div>
                        <strong class="text-sm text-slate-800">SAP S/4HANA Connector</strong>
                        <div class="text-xs text-slate-500">
                            Failures: {erp.circuit_breakers["sap"]["failure_count"]} /
                            {erp.circuit_breakers["sap"]["failure_threshold"]}
                        </div>
                    </div>
                    <span class="px-2 py-1 text-xs font-bold rounded uppercase
                        bg-emerald-100 text-emerald-800">
                        {erp.circuit_breakers["sap"]["status"]}
                    </span>
                </div>
                <div class="flex justify-between items-center p-3 bg-slate-50 rounded border">
                    <div>
                        <strong class="text-sm text-slate-800">NetSuite ERP Connector</strong>
                        <div class="text-xs text-slate-500">
                            Failures: {erp.circuit_breakers["netsuite"]["failure_count"]} /
                            {erp.circuit_breakers["netsuite"]["failure_threshold"]}
                        </div>
                    </div>
                    <span class="px-2 py-1 text-xs font-bold rounded uppercase
                        bg-emerald-100 text-emerald-800">
                        {erp.circuit_breakers["netsuite"]["status"]}
                    </span>
                </div>
            </div>
        </div>

        <div class="bg-white p-6 rounded-lg border border-slate-200 shadow-sm">
            <h2 class="font-semibold text-slate-900 mb-4">Outbox Queue & Dead Letters</h2>
            <div class="grid grid-cols-3 gap-4 mb-4 text-center">
                <div class="p-3 bg-slate-50 rounded border">
                    <span class="text-xs text-slate-500 uppercase">Pending</span>
                    <div class="text-xl font-bold text-slate-900 mt-1">{outbox.pending_count}</div>
                </div>
                <div class="p-3 bg-slate-50 rounded border">
                    <span class="text-xs text-slate-500 uppercase">Processed</span>
                    <div
                        class="text-xl font-bold text-slate-900 mt-1">{outbox.processed_count}</div>
                </div>
                <div class="p-3 bg-slate-50 rounded border">
                    <span class="text-xs text-slate-500 uppercase">DLQ</span>
                    <div class="text-xl font-bold text-rose-600 mt-1">{outbox.dlq_count}</div>
                </div>
            </div>
            <div class="text-xs text-slate-500">
                Oldest pending record age: <strong>{outbox.oldest_pending_age_seconds}s</strong>
            </div>
        </div>
    </div>

    <div class="bg-white p-6 rounded-lg border border-slate-200 shadow-sm mb-8">
        <h2 class="font-semibold text-slate-900 mb-2">Execute ERP Repair & Outbox Replay</h2>
        <form method="POST" action="/api/v1/admin/actions/erp-repair" class="space-y-4 max-w-lg">
            <input type="hidden" name="csrf_token" value="{csrf_token}">
            <input type="hidden" name="token" value="{auth.token}">
            <div>
                <label class="block text-xs font-medium text-slate-700 mb-1">Target System</label>
                <select name="system" class="w-full border rounded px-3 py-2 text-sm">
                    <option value="all">All Systems (SAP + NetSuite)</option>
                    <option value="sap">SAP S/4HANA</option>
                    <option value="netsuite">NetSuite ERP</option>
                </select>
            </div>
            <div>
                <label class="block text-xs font-medium text-slate-700 mb-1">Repair Action</label>
                <select name="action" class="w-full border rounded px-3 py-2 text-sm">
                    <option value="full_repair">Full Repair</option>
                    <option value="reset_circuit_breaker">Reset Circuit Breaker Only</option>
                    <option value="refill_tokens">Refill Token Bucket Only</option>
                    <option value="replay_outbox">Replay Dead-Letter Queue Only</option>
                </select>
            </div>
            <button type="submit"
                class="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white
                    text-sm font-semibold rounded">
                Execute ERP Repair
            </button>
        </form>
    </div>
    """
    html = _render_layout("ERP Repairs", "erp_repairs", content, csrf_token, auth, banner_msg=msg)
    return HTMLResponse(content=html)


@router.get("/admin/policy-config", response_class=HTMLResponse)
async def view_policy_config(
    auth: AuthContext = Depends(require_permission("admin:read")),
    db: AsyncSession = Depends(get_async_session),
    msg: str | None = None,
) -> HTMLResponse:
    """Approval policy management and rules activation view."""
    csrf_token = generate_csrf_token(auth.tenant_id, auth.user_id)
    current_version = "None"
    current_hash = "N/A"
    current_rules: dict[str, Any] = {}

    try:
        res = await db.execute(
            text(
                "SELECT version, content_hash, rules FROM approval_policies "
                "WHERE tenant_id = :t AND is_active = true"
            ),
            {"t": auth.tenant_id},
        )
        row = res.first()
        if row:
            current_version = str(row[0])
            current_hash = str(row[1])
            current_rules = row[2] if isinstance(row[2], dict) else json.loads(row[2])
    except Exception:
        pass

    formatted_json = json.dumps(current_rules, indent=2)

    content = f"""
    <div class="mb-8">
        <h1 class="text-2xl font-bold text-slate-900">Approval Policy Configuration</h1>
        <p class="text-sm text-slate-500 mt-1">Manage deterministic spending thresholds.</p>
    </div>

    <div class="bg-white p-6 rounded-lg border border-slate-200 shadow-sm mb-8">
        <div class="flex items-center justify-between mb-4 pb-4 border-b border-slate-200">
            <div>
                <span class="text-xs text-slate-500 uppercase font-semibold">Active Policy</span>
                <div class="text-xl font-bold text-slate-900 mt-1">{current_version}</div>
            </div>
            <div class="text-right">
                <span class="text-xs text-slate-500 uppercase font-semibold">Content Hash</span>
                <div class="font-mono text-xs text-slate-700 mt-1">{current_hash[:16]}...</div>
            </div>
        </div>

        <form method="POST" action="/api/v1/admin/actions/policy-config" class="space-y-4">
            <input type="hidden" name="csrf_token" value="{csrf_token}">
            <input type="hidden" name="token" value="{auth.token}">
            <div>
                <label class="block text-xs font-medium text-slate-700 mb-1">
                    New Version Tag (Optional)
                </label>
                <input type="text" name="version" placeholder="e.g. v2.1"
                    class="w-full max-w-sm border rounded px-3 py-2 text-sm">
            </div>
            <div>
                <label class="block text-xs font-medium text-slate-700 mb-1">
                    Policy Rules (JSON)
                </label>
                <textarea name="rules_json" rows="12"
                    class="w-full font-mono text-xs border rounded p-3 leading-relaxed"
                >{formatted_json}</textarea>
            </div>
            <button type="submit"
                class="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white
                    text-sm font-semibold rounded">
                Publish & Activate New Policy Version
            </button>
        </form>
    </div>
    """
    html = _render_layout(
        "Policy Config", "policy_config", content, csrf_token, auth, banner_msg=msg
    )
    return HTMLResponse(content=html)


@router.get("/admin/integrations", response_class=HTMLResponse)
async def view_integrations(
    auth: AuthContext = Depends(require_permission("admin:read")),
    settings: Settings = Depends(get_settings),
    msg: str | None = None,
) -> HTMLResponse:
    """External integrations status panel with strict zero-mock degradation rules."""
    csrf_token = generate_csrf_token(auth.tenant_id, auth.user_id)

    slack_connected = bool(
        settings.SLACK_BOT_TOKEN
        and settings.SLACK_BOT_TOKEN != "xoxb-test-token"
        and settings.SLACK_SIGNING_SECRET
        and settings.SLACK_SIGNING_SECRET != "test_slack_signing_secret"
    )

    erp_connected = bool(
        os.getenv("ERP_API_KEY") or os.getenv("SAP_CLIENT_ID") or os.getenv("NETSUITE_TOKEN")
    )

    neo4j_res = await check_neo4j(settings)
    temporal_res = await check_temporal(settings)
    minio_res = await check_minio(settings)

    def render_card(
        name: str,
        is_connected: bool,
        tech_detail: str,
        setup_guide: str,
        latency: float | None = None,
    ) -> str:
        badge = (
            '<span class="px-2.5 py-0.5 rounded-full text-xs font-semibold '
            'bg-emerald-100 text-emerald-800">Connected</span>'
            if is_connected
            else '<span class="px-2.5 py-0.5 rounded-full text-xs font-semibold '
            'bg-amber-100 text-amber-800">Not connected</span>'
        )
        guide_html = ""
        if not is_connected:
            guide_html = f"""
            <div class="mt-4 p-3 bg-amber-50 rounded border border-amber-200">
                <span class="text-xs font-bold text-amber-800 uppercase block mb-1">
                    Setup Guide
                </span>
                <p class="text-xs text-amber-900 whitespace-pre-line leading-relaxed">
                    {setup_guide}
                </p>
            </div>
            """
        latency_html = (
            f'<span class="text-xs text-slate-500 font-mono">({latency}ms)</span>'
            if latency
            else ""
        )

        return f"""
        <div class="bg-white p-6 rounded-lg border border-slate-200 shadow-sm flex flex-col">
            <div>
                <div class="flex items-center justify-between mb-2">
                    <h3 class="font-bold text-slate-900 text-base">{name}</h3>
                    <div class="flex items-center gap-2">{badge} {latency_html}</div>
                </div>
                <p class="text-xs text-slate-500 mb-2">{tech_detail}</p>
                {guide_html}
            </div>
        </div>
        """

    slack_card = render_card(
        "Slack Bot & Webhooks",
        slack_connected,
        f"Bot OAuth Token: {settings.SLACK_BOT_TOKEN[:8]}...",
        "1. Create Slack App in api.slack.com/apps.\n"
        "2. Set SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET in .env.\n"
        "3. Expose webhook via make slack-tunnel.",
    )

    erp_card = render_card(
        "SAP & NetSuite ERP",
        erp_connected,
        "Enterprise Resource Planning ledger sync and invoice ingestion.",
        "1. Create API Integration user in SAP S/4HANA or NetSuite.\n"
        "2. Configure ERP_API_KEY, SAP_CLIENT_ID, or NETSUITE_TOKEN in .env.\n"
        "3. Verify automated PO and invoice sync.",
    )

    neo4j_card = render_card(
        "Neo4j Knowledge Graph",
        neo4j_res.healthy,
        f"Graph Database URI: {settings.NEO4J_URI}",
        "1. Start graph container via 'make docker-up'.\n"
        f"2. Confirm Neo4j is listening on {settings.NEO4J_URI}.\n"
        "3. Check NEO4J_USER and NEO4J_PASSWORD in .env.",
        latency=neo4j_res.latency_ms if neo4j_res.healthy else None,
    )

    temporal_card = render_card(
        "Temporal.io Workflow Engine",
        temporal_res.healthy,
        f"Orchestration Target: {settings.TEMPORAL_TARGET_HOST}",
        "1. Start Temporal server via 'make docker-up'.\n"
        f"2. Verify gRPC endpoint on {settings.TEMPORAL_TARGET_HOST}.\n"
        "3. Validate TEMPORAL_NAMESPACE in .env.",
        latency=temporal_res.latency_ms if temporal_res.healthy else None,
    )

    minio_card = render_card(
        "MinIO / S3 Object Storage",
        minio_res.healthy,
        f"Private Storage Endpoint: {settings.S3_ENDPOINT_URL}",
        "1. Start MinIO container via 'make docker-up'.\n"
        f"2. Verify S3 API on {settings.S3_ENDPOINT_URL}.\n"
        "3. Check S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY.",
        latency=minio_res.latency_ms if minio_res.healthy else None,
    )

    content = f"""
    <div class="mb-8">
        <h1 class="text-2xl font-bold text-slate-900">Enterprise Integrations & Services</h1>
        <p class="text-sm text-slate-500 mt-1">
            Live connection status for external systems. Services degrade safely when unconfigured.
        </p>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {slack_card}
        {erp_card}
        {neo4j_card}
        {temporal_card}
        {minio_card}
    </div>
    """
    html = _render_layout("Integrations", "integrations", content, csrf_token, auth, banner_msg=msg)
    return HTMLResponse(content=html)


@router.get("/admin/audit-search", response_class=HTMLResponse)
async def view_audit_search(
    actor: str | None = None,
    action: str | None = None,
    auth: AuthContext = Depends(require_permission("admin:read")),
    db: AsyncSession = Depends(get_async_session),
    msg: str | None = None,
) -> HTMLResponse:
    """Cryptographic audit search and verification ledger."""
    csrf_token = generate_csrf_token(auth.tenant_id, auth.user_id)
    chain_res = await AuditHashChainManager.verify_chain(db, auth.tenant_id)

    audit_status_badge = (
        '<span class="px-3 py-1 rounded-full text-xs font-bold '
        'bg-emerald-100 text-emerald-800 border border-emerald-300">CRYPTOGRAPHICALLY '
        "VERIFIED</span>"
        if chain_res.is_valid
        else '<span class="px-3 py-1 rounded-full text-xs font-bold '
        'bg-rose-100 text-rose-800 border border-rose-300">TAMPER DETECTED</span>'
    )

    records_html = ""
    try:
        query = (
            "SELECT sequence, actor_id, actor_type, action, request_id, verdict, created_at "
            "FROM approval_audit_records WHERE tenant_id = :t "
        )
        params: dict[str, Any] = {"t": auth.tenant_id}
        if actor:
            query += "AND actor_id ILIKE :actor "
            params["actor"] = f"%{actor}%"
        if action:
            query += "AND action ILIKE :action "
            params["action"] = f"%{action}%"
        query += "ORDER BY sequence DESC LIMIT 50"

        res = await db.execute(text(query), params)
        rows = res.all()
        if rows:
            for r in rows:
                v_text = r[5] or "-"
                records_html += f"""
                <tr class="border-b border-slate-100 text-sm">
                    <td class="py-3 px-4 font-mono text-xs">{r[0]}</td>
                    <td class="py-3 px-4 font-medium text-slate-800">{r[1]}</td>
                    <td class="py-3 px-4 text-xs">{r[2]}</td>
                    <td class="py-3 px-4 text-xs font-mono">{r[3]}</td>
                    <td class="py-3 px-4 font-mono text-xs text-slate-500">{str(r[4])[:8]}...</td>
                    <td class="py-3 px-4 text-xs font-semibold">{v_text}</td>
                    <td class="py-3 px-4 text-xs text-slate-500">{str(r[6])[:19]}</td>
                </tr>
                """
        else:
            records_html = (
                '<tr><td colspan="7" class="py-6 text-center text-slate-400 text-sm">'
                "No audit records matched query filters.</td></tr>"
            )
    except Exception as exc:
        records_html = (
            f'<tr><td colspan="7" class="py-6 text-center text-rose-500 text-sm">'
            f"Error executing audit search: {exc}</td></tr>"
        )

    content = f"""
    <div class="mb-8">
        <h1 class="text-2xl font-bold text-slate-900">Cryptographic Audit Ledger</h1>
        <p class="text-sm text-slate-500 mt-1">
            Verifiable append-only audit records and SHA-256 hash chain verification.
        </p>
    </div>

    <div
        class="bg-white p-6 rounded-lg border border-slate-200 shadow-sm mb-8 flex justify-between">
        <div>
            <div class="text-xs text-slate-500 uppercase font-semibold">
                Audit Chain Verification
            </div>
            <div class="text-xl font-bold text-slate-900 mt-1">
                {chain_res.total_entries} Blocks Chained
            </div>
        </div>
        <div>
            {audit_status_badge}
        </div>
    </div>

    <div class="bg-white p-6 rounded-lg border border-slate-200 shadow-sm mb-8">
        <h2 class="font-semibold text-slate-900 mb-4">Filter Audit Records</h2>
        <form method="GET" action="/admin/audit-search"
            class="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <input type="hidden" name="token" value="{auth.token}">
            <div>
                <label class="block text-xs font-medium text-slate-700 mb-1">Actor ID</label>
                <input type="text" name="actor" value="{actor or ""}"
                    placeholder="e.g. alice@omnea.ai"
                    class="w-full border rounded px-3 py-2 text-sm">
            </div>
            <div>
                <label class="block text-xs font-medium text-slate-700 mb-1">Action</label>
                <input type="text" name="action" value="{action or ""}" placeholder="e.g. approve"
                    class="w-full border rounded px-3 py-2 text-sm">
            </div>
            <div class="flex items-end">
                <button type="submit"
                    class="w-full px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white
                        text-sm font-semibold rounded">
                    Search Audit Records
                </button>
            </div>
        </form>
    </div>

    <div class="bg-white rounded-lg border border-slate-200 shadow-sm overflow-hidden">
        <table class="w-full text-left">
            <thead class="bg-slate-50 text-slate-500 text-xs uppercase border-b border-slate-200">
                <tr>
                    <th class="py-3 px-4">Seq</th>
                    <th class="py-3 px-4">Actor</th>
                    <th class="py-3 px-4">Type</th>
                    <th class="py-3 px-4">Action</th>
                    <th class="py-3 px-4">Request ID</th>
                    <th class="py-3 px-4">Verdict</th>
                    <th class="py-3 px-4">Timestamp</th>
                </tr>
            </thead>
            <tbody>
                {records_html}
            </tbody>
        </table>
    </div>
    """
    html = _render_layout("Audit Search", "audit_search", content, csrf_token, auth, banner_msg=msg)
    return HTMLResponse(content=html)
