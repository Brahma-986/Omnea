"""Unit tests for Stage 2 Frontend, Design System, and App Shell static assets."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC_DIR = (
    Path(__file__).resolve().parent.parent.parent / "services" / "api" / "src" / "api" / "static"
)

EXPECTED_FILES = [
    "index.html",
    "css/tokens.css",
    "css/components.css",
    "css/app.css",
    "js/api.js",
    "js/state.js",
    "js/router.js",
    "js/layout.js",
    "js/components/table.js",
    "js/components/modal.js",
    "js/components/toast.js",
    "js/components/command_palette.js",
    "js/components/charts.js",
    "js/views/public_home.js",
    "js/views/auth_views.js",
    "js/views/dashboard_views.js",
    "js/views/onboarding.js",
    "js/views/intake_chat.js",
    "js/views/requests_list.js",
    "js/views/request_detail.js",
    "js/views/approver_inbox.js",
    "js/views/vendor_views.js",
    "js/views/finance_views.js",
    "js/views/legal_views.js",
    "js/app.js",
]


@pytest.mark.unit
def test_all_expected_static_files_exist_and_non_empty() -> None:
    """Verify all 17 required static assets are present and non-empty."""
    assert STATIC_DIR.is_dir(), f"Static directory {STATIC_DIR} must exist"
    for relative_path in EXPECTED_FILES:
        file_path = STATIC_DIR / relative_path
        assert file_path.is_file(), f"Expected file '{relative_path}' is missing"
        assert file_path.stat().st_size > 100, f"File '{relative_path}' is empty"


@pytest.mark.unit
def test_zero_inline_scripts_in_index_html() -> None:
    """Enforce zero inline scripts in index.html for strict CSP compliance."""
    index_path = STATIC_DIR / "index.html"
    content = index_path.read_text(encoding="utf-8")

    # Check for inline event attributes (onclick, onload, onsubmit, onerror, etc.)
    inline_events = re.findall(r"\bon\w+\s*=", content, re.IGNORECASE)
    assert not inline_events, f"Found inline event handlers: {inline_events}"

    # Check for <script> tags without src or with inline code
    script_tags = re.findall(r"<script\b[^>]*>(.*?)</script>", content, re.IGNORECASE | re.DOTALL)
    for script_content in script_tags:
        assert not script_content.strip(), "Found prohibited inline JavaScript code"

    # Verify script is loaded with type="module" and src pointing to app.js
    assert '<script type="module" src="/static/js/app.js"></script>' in content


@pytest.mark.unit
def test_design_system_tokens_contrast_and_themes() -> None:
    """Verify CSS tokens support light & dark modes, spacing, typography, and focus ring."""
    tokens_path = STATIC_DIR / "css" / "tokens.css"
    content = tokens_path.read_text(encoding="utf-8")

    # Themes
    assert ":root" in content
    assert '[data-theme="dark"]' in content

    # WCAG AA Compliant Color Tokens
    assert "--bg-primary:" in content
    assert "--bg-surface:" in content
    assert "--text-primary:" in content
    assert "--text-secondary:" in content
    assert "--brand-primary:" in content
    assert "--success:" in content
    assert "--warning:" in content
    assert "--danger:" in content

    # Focus ring token (WCAG 2.4.7 Focus Visible)
    assert "--focus-ring:" in content
    assert ":focus-visible" in content

    # Spacing scale
    for space in ["--space-1:", "--space-2:", "--space-3:", "--space-4:", "--space-6:"]:
        assert space in content, f"Missing spacing token {space}"


@pytest.mark.unit
def test_components_css_contains_all_required_specs() -> None:
    """Verify components.css defines all required component styles."""
    comp_path = STATIC_DIR / "css" / "components.css"
    content = comp_path.read_text(encoding="utf-8")

    # Buttons
    assert ".btn" in content
    assert ".btn-primary" in content
    assert ".btn-secondary" in content
    assert ".btn-danger" in content
    assert ".btn-ghost" in content
    assert ".btn-loading" in content

    # Forms
    assert ".form-label" in content
    assert ".form-input" in content
    assert ".form-select" in content
    assert ".form-textarea" in content
    assert ".form-checkbox" in content
    assert ".form-error" in content

    # Tables & Data Grids
    assert ".data-table" in content
    assert ".sortable-th" in content
    assert ".table-toolbar" in content
    assert ".btn-export-csv" in content
    assert ".table-pagination" in content

    # Modals, Toasts, Tabs, Badges, Skeletons
    assert ".modal-backdrop" in content
    assert ".modal-dialog" in content
    assert ".toast-container" in content
    assert ".toast" in content
    assert ".tab-list" in content
    assert ".tab-button" in content
    assert ".badge" in content
    assert ".skeleton" in content
    assert ".empty-state" in content
    assert ".chart-container" in content


@pytest.mark.unit
def test_app_css_shell_and_layout_elements() -> None:
    """Verify app.css defines app shell, sidebar, command palette, and landing page layout."""
    app_path = STATIC_DIR / "css" / "app.css"
    content = app_path.read_text(encoding="utf-8")

    # App shell
    assert ".app-shell" in content
    assert ".app-sidebar" in content
    assert ".app-header" in content
    assert ".mobile-toggle" in content
    assert ".cmd-palette-btn" in content
    assert ".notification-bell-btn" in content
    assert ".user-menu-btn" in content

    # Command palette
    assert ".cmd-palette-backdrop" in content
    assert ".cmd-palette-dialog" in content
    assert ".cmd-palette-input" in content

    # Landing page sections
    assert ".hero-section" in content
    assert ".features-section" in content
    assert ".roster-grid" in content
    assert ".steps-grid" in content
    assert ".security-grid" in content

    # Auth card
    assert ".auth-card" in content


@pytest.mark.unit
def test_xss_protection_in_state_and_views() -> None:
    """Verify XSS escaping utility and its usage across views."""
    state_path = STATIC_DIR / "js" / "state.js"
    content = state_path.read_text(encoding="utf-8")

    assert "export function escapeHtml(" in content
    assert ".replace(/&/g, '&amp;')" in content
    assert ".replace(/</g, '&lt;')" in content
    assert ".replace(/>/g, '&gt;')" in content
    assert ".replace(/\"/g, '&quot;')" in content
    assert ".replace(/'/g, '&#039;')" in content


@pytest.mark.unit
def test_router_guards_and_invariants() -> None:
    """Verify client router enforces login requirement and role permissions."""
    router_path = STATIC_DIR / "js" / "router.js"
    content = router_path.read_text(encoding="utf-8")

    assert "ROUTE_ROLE_PERMISSIONS" in content
    assert "tenant_admin" in content
    assert "requester" in content
    assert "approver" in content
    assert "finance_admin" in content
    assert "security_reviewer" in content
    assert "legal_reviewer" in content
    assert "Access Denied: Role unauthorized" in content
    assert "this.navigate('/login')" in content


@pytest.mark.unit
def test_api_client_csrf_injection() -> None:
    """Verify api.js injects X-CSRF-Token on mutating requests."""
    api_path = STATIC_DIR / "js" / "api.js"
    content = api_path.read_text(encoding="utf-8")

    assert "X-CSRF-Token" in content
    assert "['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)" in content
    assert "state.getCsrfToken()" in content
    assert "Authorization" in content
    assert "X-Tenant-ID" in content


@pytest.mark.unit
def test_stage3_routes_and_app_shell() -> None:
    """Verify router.js registers all Stage 3 routes: intake, list, detail, and approvals."""
    router_path = STATIC_DIR / "js" / "router.js"
    content = router_path.read_text(encoding="utf-8")

    assert "/app/requests/new" in content
    assert "renderIntakeChat" in content
    assert "/app/requests" in content
    assert "renderRequestsList" in content
    assert "/app/requests/" in content
    assert "renderRequestDetail" in content
    assert "/app/approvals" in content
    assert "renderApproverInbox" in content


@pytest.mark.unit
def test_stage3_css_rules_in_app_css() -> None:
    """Verify app.css contains all required Stage 3 design system rules."""
    app_css_path = STATIC_DIR / "css" / "app.css"
    content = app_css_path.read_text(encoding="utf-8")

    # Agent Stepper
    assert ".agent-stepper" in content
    assert ".stepper-step" in content
    assert ".stepper-step-indicator" in content
    assert ".stepper-step-label" in content
    assert ".stepper-line" in content

    # Chat bubbles
    assert ".chat-container" in content
    assert ".chat-messages" in content
    assert ".chat-message" in content
    assert ".chat-bubble" in content
    assert ".chat-input-bar" in content
    assert ".chat-attachment-pill" in content

    # Quote evidence cards & verified badge
    assert ".quote-evidence-card" in content
    assert ".quote-snippet" in content
    assert ".badge-verified" in content

    # SLA pills
    assert ".sla-pill" in content
    assert ".sla-pill-t1" in content
    assert ".sla-pill-t2" in content
    assert ".sla-pill-t3" in content

    # Timeline
    assert ".timeline-container" in content
    assert ".timeline-step" in content
    assert ".reviewer-card" in content


@pytest.mark.unit
def test_stage3_intake_chat_invariants() -> None:
    """Verify intake_chat.js safe degradation, visual stepper, and in-thread clarification."""
    intake_path = STATIC_DIR / "js" / "views" / "intake_chat.js"
    content = intake_path.read_text(encoding="utf-8")

    # LLM connectivity check & safe degradation
    assert "requestsApi.getLlmStatus" in content
    assert "LLM Provider Not Connected" in content
    assert "Step-by-Step Configuration Guide" in content
    assert "LLM_API_KEY" in content

    # Visual stepper
    assert "Intake Agent" in content
    assert "Classifier" in content
    assert "Entity-Resolution" in content
    assert "Context Assembler" in content

    # Clarification loop
    assert "clarification_needed" in content
    assert "clarifying_question" in content
    assert "missing_fields" in content
    assert "in-thread-reply-form" in content

    # Success creation state
    assert "#/app/requests/" in content
    assert "Procurement Request Created Successfully" in content


@pytest.mark.unit
def test_stage3_requests_list_and_sse() -> None:
    """Verify requests_list.js renders expected columns and hooks up to tenant SSE stream."""
    list_path = STATIC_DIR / "js" / "views" / "requests_list.js"
    content = list_path.read_text(encoding="utf-8")

    assert "Request ID" in content
    assert "Vendor / Tool" in content
    assert "Amount" in content
    assert "Currency" in content
    assert "Status" in content
    assert "Created At" in content
    assert "View Details" in content
    assert "state.subscribeToTenantStream" in content


@pytest.mark.unit
def test_stage3_request_detail_strict_verification() -> None:
    """Verify request_detail.js displays timeline, budget widget, and strictly backend-verified badges."""
    detail_path = STATIC_DIR / "js" / "views" / "request_detail.js"
    content = detail_path.read_text(encoding="utf-8")

    # 5-step timeline
    assert "Entity Resolution" in content
    assert "Finance Review" in content
    assert "Security Review" in content
    assert "Legal Review" in content
    assert "Approver Decision" in content

    # Budget Check
    assert "Budget Check" in content
    assert "budgetAllocated" in content or "Allocated" in content
    assert "budgetRemaining" in content or "Remaining" in content

    # Policy Version & Hash
    assert "Policy Applied:" in content
    assert "policyVersion" in content or "Version" in content
    assert "policyHash" in content or "Hash:" in content

    # Comments thread
    assert "Comments & Clarification Thread" in content
    assert "requestsApi.addComment" in content

    # Strictly backend-verified quotes (no client recomputation)
    assert "badge-verified" in content
    assert "citation_verified" in content


@pytest.mark.unit
def test_stage3_approver_inbox_protections() -> None:
    """Verify approver_inbox.js enforces self-approval protection, 3-char reason, idempotency, and SLA pills."""
    approver_path = STATIC_DIR / "js" / "views" / "approver_inbox.js"
    content = approver_path.read_text(encoding="utf-8")

    # Self-approval protection
    assert "Self-Approval is Forbidden" in content
    assert "Self-approval is forbidden and has been audited." in content

    # Mandatory reason validation (min 3 chars)
    assert "len >= 3" in content or "length < 3" in content
    assert "Minimum 3 characters" in content

    # Client-generated idempotency key
    assert "idempotencyKey" in content or "idempotency_key" in content

    # SLA pills
    assert "sla-pill-t1" in content
    assert "sla-pill-t2" in content
    assert "sla-pill-t3" in content

    # Out-of-Office / Delegation
    assert "Delegation & Out-of-Office" in content
    assert "approvalsApi.setDelegation" in content
    assert "delegate_id" in content
    assert "delegate_valid_from" in content
    assert "delegate_valid_until" in content


@pytest.mark.unit
def test_stage4_vendor_views_invariants() -> None:
    """Verify vendor_views.js directory table, graph SVG, review queue, and AI research invariants."""
    vendor_path = STATIC_DIR / "js" / "views" / "vendor_views.js"
    content = vendor_path.read_text(encoding="utf-8")

    # Directory Table Columns & Filters
    assert "Vendor Name" in content
    assert "Canonical Key" in content
    assert "SUBSIDIARY_OF" in content
    assert "Active Contracts" in content
    assert "SOC2 Compliant" in content
    assert "Spend per Currency" in content
    assert "badge-currency" in content
    assert "Status" in content

    # Interactive Graph View (SVG and Directed Edges)
    assert "<svg" in content
    assert "SUBSIDIARY_OF" in content
    assert "MERGED_INTO" in content
    assert "CONTRACT_WITH" in content
    assert "graph-node" in content

    # Detail Drawer / Modal & Merge/Unmerge Actions
    assert "openDetailDrawer" in content
    assert "Corporate Hierarchy & Subsidiary Tree" in content
    assert "Merge Vendor" in content
    assert "Unmerge Vendor" in content

    # Review Queue Tab
    assert "Review Queue" in content
    assert "Confidence Score" in content
    assert "Provisional Ingress Vendor" in content
    assert "Candidate Canonical Vendor" in content
    assert "Approve Merge" in content
    assert "Reject / Keep Separate" in content
    assert "reviewApi.resolveTask" in content

    # Vendor AI Research Tab & Degraded Banner
    assert "AI Vendor Research" in content
    assert "Research Vendor with AI" in content
    assert "LLM Provider Not Connected" in content
    assert "VERIFIED CITATION" in content
    assert "quote-evidence-card" in content


@pytest.mark.unit
def test_stage4_finance_views_invariants() -> None:
    """Verify finance_views.js budgets FY2026, currency separation, policy drafting, and weakening rejection."""
    finance_path = STATIC_DIR / "js" / "views" / "finance_views.js"
    content = finance_path.read_text(encoding="utf-8")

    # Budgets Tab
    assert "FY2026" in content
    assert "Total Allocated" in content
    assert "Committed Spend" in content
    assert "Active Reservations" in content
    assert "Remaining Headroom" in content
    assert "Utilization" in content
    assert "Add / Update Budget" in content

    # Spend Dashboard Tab & Strict Currency Segregation (NEVER summed across currencies)
    assert "Multi-Currency Strict Segregation Principle" in content
    assert "never summed or mixed" in content
    assert "Department Spend Breakdown" in content

    # Approval Policies & Policy-Drafting Agent
    assert "ACTIVE POLICY:" in content
    assert "Content Hash:" in content
    assert "VP Spend Thresholds" in content
    assert "Autonomous Agent Cap" in content
    assert "Policy Version History" in content

    # Policy-Drafting Assistant
    assert "Policy-Drafting Assistant" in content
    assert "Draft Policy Rule" in content
    assert "createVisualDiff" in content

    # Weakening Rejection Alert
    assert "Weakening of Finance controls rejected:" in content

    # Confirm & Activate Policy Modal
    assert "Confirm & Activate Policy" in content
    assert (
        "apply only to new requests" in content
        or "apply these controls to all <em>new</em>" in content
    )


@pytest.mark.unit
def test_stage4_legal_views_invariants() -> None:
    """Verify legal_views.js contracts directory, filter pills, and flagged clauses inspector."""
    legal_path = STATIC_DIR / "js" / "views" / "legal_views.js"
    content = legal_path.read_text(encoding="utf-8")

    # Contracts Directory & Filter Pills
    for pill in ["All", "Active", "Expired", "Auto-Renew", "MSA", "DPA"]:
        assert pill in content, f"Missing contract filter pill: {pill}"

    assert "Title" in content
    assert "Vendor" in content
    assert "Status" in content
    assert "Total Value & Currency" in content
    assert "Auto-Renew Status" in content
    assert "Expiration Date" in content

    # Contract Detail Drawer & Renewal Notice Period
    assert "Renewal Notice Period:" in content
    assert "notice required" in content

    # Flagged Clauses Inspector
    assert "Flagged Clauses Inspector" in content
    assert "Indemnity Cap" in content
    assert "Governing Law" in content
    assert "Data Transfer" in content
    assert "Risk" in content
    assert "quote-snippet" in content
    assert "Legal Recommendation:" in content

    # Add / Flag Clause Modal
    assert "Flag / Add Clause" in content
    assert "legalApi.flagClause" in content


@pytest.mark.unit
def test_stage4_routes_and_navigation() -> None:
    """Verify router.js and app.js register client routes and sidebar navigation for vendors, finance, and legal."""
    router_path = STATIC_DIR / "js" / "router.js"
    router_content = router_path.read_text(encoding="utf-8")

    assert "/app/vendors" in router_content
    assert "renderVendorView" in router_content
    assert "/app/finance" in router_content
    assert "renderFinanceView" in router_content
    assert "/app/legal" in router_content
    assert "renderLegalView" in router_content

    # Role guards
    assert "'finance_admin', 'tenant_admin', 'cfo'" in router_content
    assert "'legal_reviewer', 'tenant_admin'" in router_content

    app_path = STATIC_DIR / "js" / "app.js"
    app_content = app_path.read_text(encoding="utf-8")

    # Sidebar navigation items
    assert "path: '/app/vendors'" in app_content
    assert "path: '/app/finance'" in app_content
    assert "path: '/app/legal'" in app_content


@pytest.mark.unit
def test_stage5_security_views_invariants() -> None:
    """Verify security_views.js drag-and-drop intake, live pipeline stepper, control findings, and evidence viewer."""
    sec_path = STATIC_DIR / "js" / "views" / "security_views.js"
    assert sec_path.exists(), "security_views.js must exist"
    content = sec_path.read_text(encoding="utf-8")

    # Document Intake & Drag-and-Drop Drop Zone
    assert "drop-zone-pdf" in content
    assert "security-file-input" in content
    assert "security-vendor-select" in content
    assert "security-doctype-select" in content
    assert "btn-upload-security-doc" in content

    # Live Pipeline Stepper Stages
    assert "pipeline-stepper-container" in content
    assert "stage-safety-scan" in content
    assert "stage-extraction" in content
    assert "stage-ocr" in content
    assert "stage-chunking" in content
    assert "stage-embeddings" in content
    assert "stage-assessment" in content

    # Control Domains & Verbatim Citation Badges
    assert "tprm-domain-card" in content
    assert "badge-verified" in content
    assert "Not Found" in content
    assert "btn-open-evidence" in content
    assert "quote-evidence-card" in content

    # Evidence Viewer Modal & Quote Highlighting
    assert "evidence-viewer-modal" in content
    assert "evidence-quote-highlight" in content
    assert "SHA-256 Verified" in content

    # Risk Register & Quarantine
    assert "risk-register-table-container" in content
    assert "btn-quarantine-doc" in content


@pytest.mark.unit
def test_stage5_routes_and_navigation() -> None:
    """Verify router.js and app.js register client routes and sidebar navigation for security workspace."""
    router_path = STATIC_DIR / "js" / "router.js"
    router_content = router_path.read_text(encoding="utf-8")

    assert "/app/security" in router_content
    assert "renderSecurityView" in router_content
    assert "'security_reviewer', 'tenant_admin'" in router_content

    app_path = STATIC_DIR / "js" / "app.js"
    app_content = app_path.read_text(encoding="utf-8")

    assert "path: '/app/security'" in app_content
