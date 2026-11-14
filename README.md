# AI-Native Procurement Orchestration Platform

An enterprise agentic operating system for procurement and supplier management.

## Architectural Layers
1. **Omni-channel Ingress Engine**: Normalizes webhooks from Slack, MS Teams, and email into canonical internal events.
2. **Intent & Routing Fabric**: LLM-based structured extraction, routing to LangGraph agent pools; document parsing via LlamaIndex.
3. **Enterprise Context Engine**: Neo4j knowledge graph (Vendors, Contracts, Subsidiaries, Approvals) + pgvector semantic vector store.
4. **Systems-of-Record Sync Layer**: Transactional, idempotent writes to SAP / NetSuite / Workday orchestrated by Temporal.io.

---

## Quickstart

### Prerequisites
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (fast Python package manager)
- Docker & Docker Compose

### 1. Setup Environment
```bash
cp .env.example .env
uv sync --all-packages
```

### 2. Start Infrastructure
Start PostgreSQL (pgvector), Neo4j, MinIO, Temporal server + UI, and the API container:
```bash
make docker-up
```

### 3. Run Database Migrations
Apply Alembic migrations to create tables (`requests`, `approvals`, `audit_log`, `workspace_installations`, `inbound_event_queue`, `conversation_sessions`):
```bash
make migrate
```

### 4. Verify Health
Check that all backing dependencies are reachable:
```bash
curl http://localhost:8000/health
```

Expected output:
```json
{
  "status": "ok",
  "timestamp": "2026-11-12T22:00:00Z",
  "dependencies": {
    "postgres": { "name": "postgres", "healthy": true, "latency_ms": 3.2 },
    "neo4j": { "name": "neo4j", "healthy": true, "latency_ms": 5.1 },
    "temporal": { "name": "temporal", "healthy": true, "latency_ms": 4.0 },
    "minio": { "name": "minio", "healthy": true, "latency_ms": 2.5 }
  }
}
```

---

## Slack Integration Setup

### 1. Create Slack App from Manifest
1. Navigate to [api.slack.com/apps](https://api.slack.com/apps).
2. Click **Create New App** -> **From an app manifest**.
3. Select your workspace and paste the JSON from `infra/slack/manifest.json`.
4. Copy your **Signing Secret** from *Basic Information* into `.env` as `SLACK_SIGNING_SECRET`.
5. Install the app to your workspace and copy the **Bot User OAuth Token** (`xoxb-...`) into `.env` as `SLACK_BOT_TOKEN`.

### 2. Configure Local Webhook Tunnel
Expose your local port 8000 to the internet via HTTPS:
```bash
make slack-tunnel
```
Copy the generated public HTTPS URL (e.g. `https://xxxx.ngrok-free.app`) and update the Request URLs in your Slack App Settings:
- **Events API**: `https://xxxx.ngrok-free.app/api/v1/ingress/slack/events`
- **Slash Commands (`/procure`)**: `https://xxxx.ngrok-free.app/api/v1/ingress/slack/commands`

---

## Development Commands
```bash
make lint       # Ruff check & format validation
make typecheck  # Mypy strict typecheck
make test-unit  # Unit test suite
make test       # Full test suite
```
