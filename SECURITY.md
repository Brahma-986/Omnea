# Security Policy & Architecture

Omnea Procure OS is engineered with defense-in-depth principles designed for mission-critical enterprise procurement environments.

---

## Reporting Vulnerabilities

If you discover a security vulnerability within Omnea Procure OS, please do **NOT** disclose it publicly via GitHub issues. Instead, report it immediately to our security response team:

- **Email**: security@omnea.internal
- **PGP Fingerprint**: (Available upon request)
- **Response SLA**: Acknowledgment within 24 hours; remediation timeline provided within 72 hours.

---

## Core Security Controls & Architecture

### 1. Dual-Wall PostgreSQL Row-Level Security (RLS)
Multi-tenancy is enforced through dual-wall defense:
- **Application Context**: All authenticated API requests verify the signed JWT claim and establish an async context variable (`tenant_id_ctx`). Missing or empty tenant claims result in immediate HTTP 401 Unauthorized.
- **Database Engine RLS**: Every tenant-partitioned table (`requests`, `approvals`, `vendors`, `department_budgets`, `audit_log`, `api_keys`, `webhooks`) enforces PostgreSQL Row-Level Security policies checking `tenant_id = current_setting('rls.tenant_id')`. Queries attempting cross-tenant access fail closed at the database driver level.

### 2. Strict Content Security Policy (Zero CDN Dependency)
The web application and API documentation strictly reject third-party origin scripts:
- **Policy Header**: `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none';`
- **Zero External CDNs**: All client libraries, UI widgets, and Swagger UI assets (`swagger-ui.css`, `swagger-ui-bundle.js`) are vendorized locally under `/static`.
- **Anti-Clickjacking**: `X-Frame-Options: DENY` and `frame-ancestors 'none'` prevent embedding in malicious iframes.

### 3. Cryptographic Merkle Audit Hash Chain
Every sensitive state change (approval, policy override, ERP sync, vendor merge) writes an immutable record to `audit_log`:
- Each entry calculates `sha256(previous_hash + sequence_number + tenant_id + action + entity_id + payload)`.
- The hash chain is continuously verifiable via `GET /api/v1/admin/audit-chain`. Any manual database alteration or truncation breaks chain integrity and triggers immediate alerts.

### 4. Zero Plaintext Secrets in Storage & Logs
- **Credential Storage**: Third-party ERP credentials (NetSuite tokens, SAP passwords) and Slack tokens are encrypted using AES-256-GCM via the Encrypted Secrets Manager. The database stores only abstract vault references (`secret_ref`), never plaintext credentials.
- **Log Redaction**: A custom `RedactingFilter` intercepts all log records across Uvicorn and Python loggers, automatically scrubbing JWT patterns (`eyJ...`), Authorization Bearer/Basic headers, API keys (`omnea_live_...`), and query parameters (`token=`, `ticket=`).
- **Single-Use Stream Tickets**: SSE event streams authenticate via `httpOnly` session cookies or short-lived (~30s TTL) single-use tickets, preventing access token exposure in URLs and browser history.

### 5. OS-Isolated Process Sandboxing & File Quarantine
- **Untrusted File Parsing**: SOC 2, ISO, and vendor DPA PDF files are parsed inside an OS-isolated subprocess enforcing strict memory and CPU runtime ceilings.
- **Malware Scanning & Quarantine**: Uploaded documents are held in quarantine status until structural PDF scanning and malware checks complete. Hex-escaped names and rogue object streams are automatically rejected.

### 6. Public Deployment Protection: Rate Limits & Spend Caps
- **Sliding-Window Rate Limiting**: In-memory sliding windows throttle traffic across Global (1200 req/min), Tenant (300 req/min), and Client IP (120 req/min) boundaries, returning HTTP 429 with `Retry-After`.
- **Hard Daily LLM Spend Caps**: Autonomous LLM invocations calculate exact completion costs and track daily consumption per tenant and globally. If daily limits ($25 global, $5 per tenant) are reached, further calls raise `LLMSpendBudgetExceededError` and return HTTP 429, eliminating cloud cost overrun risks.
