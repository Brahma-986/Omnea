# 3-Minute Platform Demo Script

This script provides an exact, step-by-step 3-minute walkthrough demonstrating every platform requirement mapped to a distinct screen and user interaction.

---

### Minute 0:00 - 0:30 | Intake & Real-Time Dashboard
- **Screen**: Request Intake (`/app`) & Real-Time Dashboard (`/app/dashboard`)
- **Action**:
  1. Open the web application and submit a natural language purchase request: *"We need 50 licenses of Figma Enterprise for the design team ($45,000/year)."*
  2. Notice structured intent extraction: Vendor (`Figma`), Estimated Spend (`$45,000`), Department (`Design`), Term (`Annual`).
  3. Navigate to Dashboard: The real-time SSE stream updates live metrics with zero page reloads via single-use tickets (`POST /api/v1/events/ticket`).

---

### Minute 0:30 - 1:00 | Human-in-the-Loop Approvals & Finance Budgets
- **Screen**: Approval Inbox (`/app/approvals`) & Finance Workspace (`/app/finance`)
- **Action**:
  1. Switch to Approver view: The request flags an automated VP threshold escalation because spend exceeds the $25,000 policy threshold.
  2. View Finance Budget Headroom: The Design department budget shows $100,000 allocated, $45,000 reserved, with $55,000 remaining headroom verified.
  3. Click **Approve**: Decision is cryptographically written to the Merkle audit chain with a tamper-evident SHA-256 link.

---

### Minute 1:00 - 1:30 | TPRM Security Evidence & Quarantine
- **Screen**: Security Reviewer Workspace (`/app/security`)
- **Action**:
  1. Drag and drop vendor security artifact: `SOC2_Type2_Report.pdf`.
  2. The OS-isolated extraction process runs with memory limits, verifying control domains (Encryption, Access Control, Incident Response).
  3. Click a control finding: The Evidence Viewer highlights the verified quote on Page 14 with character offsets.
  4. Drag and drop a hostile PDF test file: The structural scanner flags suspicious object streams and immediately moves the file into Quarantine.

---

### Minute 1:30 - 2:00 | Immutable Ledger ERP & Saga Reconciliation
- **Screen**: ERP Systems of Record (`/app/erp`) & Admin Repairs (`/admin/erp-repairs`)
- **Action**:
  1. Following approval, the idempotent distributed saga executes.
  2. Inspect the built-in Immutable Ledger ERP: Exactly one Purchase Order (`PO-2026-XXXX`) is created with commitments recorded.
  3. Click **Run Reconciliation**: The engine audits ERP state against request records, reporting 0 unhandled drift on clean state.

---

### Minute 2:00 - 2:30 | Developer Platform & SDKs
- **Screen**: API Documentation (`/docs`) & Webhooks Portal (`/app/settings`)
- **Action**:
  1. Open `/docs`: Self-hosted Swagger UI loads under strict CSP (`script-src 'self'`) with zero CDN dependencies.
  2. Navigate to API Keys: Generate a scoped key (`omnea_live_...`). Hashed via constant-time SHA-256; displayed once.
  3. Test Webhooks: Trigger an event; inspect delivery log with HMAC-SHA256 signature verification.

---

### Minute 2:30 - 3:00 | Desktop Native App & Public Status Safeguards
- **Screen**: Desktop Client & Public Status (`/status`)
- **Action**:
  1. Open desktop application: Tokens retrieved securely from OS keychain; test deep link `procure://request/REQ-101`.
  2. Open Public Status Page (`/status`): Shows real-time operational status across API, Database, Storage, and Workers.
  3. Review Public Deployment Safeguards: Displays the active Global LLM Daily Spend Cap ($25.00) and sliding-window rate limits, guaranteeing cost protection.
