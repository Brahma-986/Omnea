# Omnea Procurement Platform: Claims Register & Verification Matrix

This register transparently documents the verification status, automated test evidence, and known live-system boundaries for every capability across Stages 1 through 9.

---

## Verification Status Taxonomy

- **VERIFIED IN AUTOMATED SUITE**: Validated by automated unit, integration, mutation, or end-to-end Playwright tests running against actual code and database fixtures.
- **VERIFIED VIA INJECTED INTERFACE / SIMULATOR**: The core business logic, state machines, and interfaces are verified against realistic mock/fake providers (e.g., FakeLLMClient, InjectedSlackClient, MockERPConnector).
- **KNOWN GAP / UNRUN LIVE-SYSTEM BOUNDARY**: Feature requires external production credentials or third-party enterprise tenant environments (e.g., live NetSuite production ERP, paid Slack enterprise grid workspace, live ClamAV daemon) that were not wired to live networks during test execution.

---

## Comprehensive Capability Matrix

| Stage | Capability Claim | Verification Status | Automated Test & Evidence File | Known Live-System Boundaries & Notes |
| :--- | :--- | :--- | :--- | :--- |
| **Stage 1** | Email & Password Org Signup & Verification | **VERIFIED IN AUTOMATED SUITE** | `tests/integration/test_stage1_auth_acceptance.py`<br/>`tests/e2e/test_stage9_live_acceptance.py` | Full flow tested with token email verification. |
| **Stage 1** | Role-Based Access Control (5 Roles) | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_api_permissions.py`<br/>`tests/integration/test_stage1_auth_acceptance.py` | Strict 403 matrix on cross-role unauthorized access. |
| **Stage 1** | Dual-Wall PostgreSQL Row-Level Security (RLS) | **VERIFIED IN AUTOMATED SUITE** | `tests/integration/test_rls_matrix_and_dual_wall.py`<br/>`tests/unit/test_security_rls.py` | Verified dual-wall: both DB RLS session var and application claim required. |
| **Stage 1** | Cryptographic Merkle Audit Hash Chain | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_admin_routes.py` | Validated SHA-256 sequential linking; detects tampering. |
| **Stage 1** | TOTP 2FA & Session Revocation | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_auth_security.py` | Single-use refresh token rotation and role-change revocation verified. |
| **Stage 2** | Real-Time SSE Stream with Single-Use Tickets | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_sse_ticket_and_log_redaction.py` | Single-use ~30s tickets; zero JWT or ticket leaks in logs. |
| **Stage 2** | Role-Specific Metrics Dashboard | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_stage2_realtime_dashboard.py` | Requester, approver, finance, legal, security dashboards verified. |
| **Stage 2** | Frontend Query Cache & Deduping | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_frontend_query_cache.py` | In-flight deduplication and TTL caching verified in Node.js runtime. |
| **Stage 3** | Natural Language Request Intake | **VERIFIED VIA INJECTED INTERFACE** | `tests/unit/test_stage3_approvals_api.py` | Tested with schema-conforming FakeLLMClient. |
| **Stage 3** | Autonomous Approval Policy & Escalation | **VERIFIED IN AUTOMATED SUITE** | `tests/eval/test_approval_golden_eval.py`<br/>`tests/unit/test_stage3_approvals_api.py` | Evaluated against golden cases for VP spend and threshold rules. |
| **Stage 3** | Delegation & Step Reassignment | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_stage3_approvals_api.py` | Delegation lifecycle, expiration, and audit logging tested. |
| **Stage 4** | Knowledge Graph & Vendor Entity Resolution | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_resolution_pipeline_details.py`<br/>`tests/eval/test_entity_resolution_benchmark.py` | Jaro-Winkler, character trigram, and hard invalidation guards verified. |
| **Stage 4** | Department Budget Commitment & Headroom | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_stage4_finance_legal_api.py` | Atomic budget reservations, overage prevention, spend dashboard. |
| **Stage 4** | Legal Contract Flagging & Clause Analysis | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_stage4_finance_legal_api.py` | Contract clauses flagged, high-liability indicators tested. |
| **Stage 5** | OS-Isolated Process Sandbox for Extraction | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_stage5_tprm_pipeline.py` | Memory/CPU limits via sub-process sandbox and structural PDF check. |
| **Stage 5** | Hostile PDF Structural Scan & Quarantine | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_stage5_tprm_pipeline.py` | Malicious PDFs quarantined; suspicious hex-streams blocked. |
| **Stage 5** | Evidence Viewer & Offset Verification | **VERIFIED IN AUTOMATED SUITE** | `tests/eval/test_tprm_golden_eval.py`<br/>`tests/unit/test_tprm_mutation_harness.py` | Verified exact citation page, offsets, and mutated-quote dropping. |
| **Stage 5** | Live ClamAV Daemon Integration | **KNOWN GAP / UNRUN BOUNDARY** | N/A | Interface verified via ClamAV quarantine scanner stub; live ClamAV daemon is unrun. |
| **Stage 6** | Built-In Immutable Ledger ERP | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_stage6_erp_saga.py` | Ledger PO generation, commitment tracking, zero secrets in DB. |
| **Stage 6** | Idempotent Distributed Saga & Kill Recovery | **VERIFIED IN AUTOMATED SUITE** | `tests/integration/test_stage6_worker_crash_recovery.py` | Crash-recovery tested at 4 named failure points with idempotency replay. |
| **Stage 6** | Reconciliation Engine & Audit Repairs | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_stage6_reconciliation.py` | Seeded drift detection with zero false positives on clean state. |
| **Stage 6** | Slack Ingress & Interactive Buttons | **VERIFIED VIA INJECTED INTERFACE** | `tests/unit/test_stage6_slack_and_notifications.py` | Injected Slack client verified; live Slack enterprise app install is unrun. |
| **Stage 6** | Live NetSuite / SAP Cloud Connectors | **KNOWN GAP / UNRUN BOUNDARY** | N/A | Connectors use unified ErpConnector interface; live NetSuite/SAP endpoints unrun. |
| **Stage 7** | REST API v1 & OpenAPI Specification | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_openapi_spec.py` | Strict validation against FastAPI schema; zero broken references. |
| **Stage 7** | Scoped API Keys & Rate Limiting | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_stage7_api_keys.py`<br/>`tests/unit/test_stage7_mutation_checks.py` | Constant-time SHA-256 verification, sliding-window limits, RLS enforcement. |
| **Stage 7** | Outbound Webhooks with HMAC-SHA256 Signatures| **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_stage7_webhooks.py`<br/>`tests/integration/test_stage7_examples_e2e.py` | HMAC signing, backoff replay, delivery logs, example receiver verification. |
| **Stage 7** | Python & TypeScript SDKs | **VERIFIED IN AUTOMATED SUITE** | `tests/integration/test_stage7_examples_e2e.py` | Verified end-to-end against live API using example scripts. |
| **Stage 8** | Desktop Electron Shell Packaging & Security | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_desktop_packaging.py` | `contextIsolation=true`, `nodeIntegration=false`, strict local CSP verified. |
| **Stage 8** | OS Keychain & Offline Local Cache | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_stage8_desktop_bridge_and_offline.py` | Offline caching, deep-link protocol (`procure://`), native desktop notifications. |
| **Stage 9** | Strict Content Security Policy (No CDN) | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_swagger_csp_and_self_hosting.py`<br/>`tests/e2e/test_swagger_playwright_csp.py` | Swagger UI self-hosted locally; zero CDN script execution; nosniff headers. |
| **Stage 9** | Public Status Dashboard (`/status`) | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_stage9_deployment_and_spend_caps.py`<br/>`tests/e2e/test_stage9_live_acceptance.py` | Live HTML status page and JSON health telemetry verified. |
| **Stage 9** | Global & Per-Tenant Rate Limiter Middleware | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_stage9_deployment_and_spend_caps.py` | Sliding-window request throttling with Retry-After 429 response. |
| **Stage 9** | Hard LLM Daily Spend Budget Caps | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_stage9_deployment_and_spend_caps.py` | Global ($25) and tenant ($5) daily budget caps block cost overruns. |
| **Stage 9** | Render Blueprint Infrastructure as Code | **VERIFIED IN AUTOMATED SUITE** | `tests/unit/test_stage9_deployment_and_spend_caps.py` | `render.yaml` schema validated; migration preDeploy, workers, and env bindings. |

---

## Explicit List of Unrun Live-System Tests & Known Gaps

1. **Live NetSuite / SAP ERP Cloud APIs**: The platform interacts through the unified `ErpConnector` interface. Tests run against the built-in Ledger ERP connector. Live SOAP/REST NetSuite and SAP environments were not called due to absence of production vendor credentials.
2. **Live ClamAV Daemon Socket**: PDF malware scanning was verified via the quarantine sandbox interface and structural scanner; a live clamd daemon socket was not connected in test runs.
3. **Live Slack Workspace Installation**: Slack OAuth handshake and interactive button callbacks are verified with signed payloads against the injected Slack interface; an automated click inside a live Slack desktop client was not performed.
4. **Live Temporal Cloud Production Cluster**: Orchestration tests run against local Temporal test services and mock workflows; connecting to a paid `*.tmprl.cloud:7233` cluster requires customer-specific mTLS certificates.
