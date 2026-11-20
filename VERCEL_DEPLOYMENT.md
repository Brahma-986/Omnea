# Omnea Procure OS - Vercel Free Tier (Hobby) Deployment Guide

This guide provides end-to-end instructions for deploying the **Omnea AI-Native Autonomous Procurement Orchestration Platform** on the **Vercel Hobby (Free Tier)** alongside zero-cost external cloud services (Supabase or Neon for PostgreSQL, Supabase Storage or Cloudflare R2 for S3 attachments, Google AI Studio for Gemini LLM), requiring **zero credit card or payment information**.

---

## Architecture Overview on Vercel

```mermaid
flowchart TD
    subgraph Clients["Clients"]
        Browser["Modern Web Browser / SPA"]
        Mobile["Mobile / Desktop Clients"]
    end

    subgraph VercelEdge["Vercel Global Edge Network (Hobby Tier)"]
        CDN["Vercel Edge CDN (public/ & /static/*)"]
        PyServerless["Python Serverless Runtime (@vercel/python - api/index.py)"]
        Rewrites["vercel.json Routing & Security Headers"]
    end

    subgraph FreeExternalServices["Free-Tier External Cloud Infrastructure (No Card Required)"]
        Postgres["PostgreSQL 16 + pgvector (Supabase or Neon Free Tier)"]
        S3Storage["S3 Object Storage (Supabase Storage or Cloudflare R2 Free)"]
        GeminiAPI["Google AI Studio (Gemini 1.5 Flash Free Tier)"]
    end

    Browser --> Rewrites
    Mobile --> Rewrites
    Rewrites -->|Static HTML/CSS/JS| CDN
    Rewrites -->|/api/*, /auth/*, /status| PyServerless
    PyServerless -->|SQLAlchemy NullPool| Postgres
    PyServerless -->|aioboto3| S3Storage
    PyServerless -->|LiteLLM REST| GeminiAPI
```

---

## 1. How to Connect GitHub to Vercel

1. Log in to your [Vercel Dashboard](https://vercel.com).
2. Click **Add New...** → **Project**.
3. Under **Import Git Repository**, select your GitHub account and locate `Brahma-986/Omnea`.
   *(If prompted, click "Adjust GitHub App Permissions" to grant Vercel access to the repository).*
4. Click **Import** next to `Brahma-986/Omnea`.

---

## 2. Project Root Directory Configuration

- **Root Directory**: Leave as `./` (Project Root).
- Do not set the root directory to `services/api` or `public`. The root directory contains `vercel.json`, `api/index.py`, `package.json`, and all Python workspace modules.

---

## 3. Build and Output Settings

In the Vercel Project Configuration screen under **Build and Output Settings**:

- **Framework Preset**: Select **Other** (or leave as Auto-Detected).
- **Build Command**: `npm run build` (Overrides default; runs `node scripts/build-frontend.js` to compile and synchronize static assets to `public/`).
- **Output Directory**: `public` (Overrides default; tells Vercel's Edge CDN where to serve static HTML, CSS, JS, and Swagger assets).
- **Install Command**: `npm install` (Installs frontend scripts, while Vercel's Python runtime automatically installs `requirements.txt`).

---

## 4. Backend Deployment Configuration

Vercel detects `api/index.py` and deploys it using `@vercel/python`:
- **Runtime**: Python 3.11 Serverless Function.
- **Entry Point**: `api/index.py` exposing FastAPI ASGI instance `app`.
- **Max Duration**: 10 seconds (standard Vercel Hobby timeout limit).
- **Memory**: 1024 MB.
- **Connection Management**: Configured with SQLAlchemy `NullPool` (via `DB_SERVERLESS_POOL=true` or automatic detection of `VERCEL=1`), closing database connections after each invocation to prevent pool exhaustion.
- **File System**: Writable operations safely target `/tmp` (`/tmp/omnea_secrets` and `/tmp/omnea_quarantine`), preventing read-only filesystem errors.

---

## 5. Environment Variables to Configure in Vercel

Go to **Project Settings** → **Environment Variables** in Vercel and add the following keys:

| Environment Variable | Required | Example / Description | Free Tier Provider |
| :--- | :---: | :--- | :--- |
| `DATABASE_URL` | **Yes** | `postgresql://postgres.[ref]:[pass]@aws-0-[region].pooler.supabase.com:6543/postgres?sslmode=require` | Supabase / Neon |
| `DB_SERVERLESS_POOL` | **Yes** | `true` (Enforces connection closure after requests) | Built-in |
| `GEMINI_API_KEY` | **Yes** | `AIzaSy...` (from [Google AI Studio](https://aistudio.google.com/)) | Google AI Studio |
| `LLM_MODEL` | **Yes** | `gemini/gemini-1.5-flash` | Google AI Studio |
| `JWT_SECRET` | **Yes** | Generate with `openssl rand -hex 32` (minimum 32 characters) | Self-generated |
| `CSRF_SECRET_KEY` | **Yes** | Generate with `openssl rand -hex 32` | Self-generated |
| `APP_ENV` | **Yes** | `production` | Built-in |
| `LOG_LEVEL` | **Yes** | `INFO` | Built-in |
| `FRONTEND_URL` | Optional | `https://your-project.vercel.app` (Automatically permitted via regex) | Vercel |
| `S3_ENDPOINT` | Optional | `https://[ref].supabase.co/storage/v1/s3` | Supabase Storage / R2 |
| `S3_ACCESS_KEY_ID` | Optional | Storage access key ID | Supabase Storage / R2 |
| `S3_SECRET_ACCESS_KEY` | Optional | Storage secret access key | Supabase Storage / R2 |
| `S3_BUCKET_ATTACHMENTS`| Optional | `procure-attachments` | Supabase Storage / R2 |
| `EMAIL_PROVIDER` | Optional | `fake` or `resend` (Free: 100 emails/day on Resend) | Resend |
| `RESEND_API_KEY` | Optional | `re_...` | Resend |

---

## 6. Database Setup Instructions (Free PostgreSQL)

### Option A: Supabase (Recommended)
1. Sign up for a free account at [supabase.com](https://supabase.com) (no credit card required).
2. Click **New Project**, choose a project name, database password, and your nearest AWS region.
3. Once provisioned, navigate to **Project Settings** → **Database**.
4. In the **Connection string** section:
   - Select the **Transaction Pooler** (URI mode, port `6543`).
   - Copy the URI:
     ```text
     postgresql://postgres.[PROJECT_REF]:[PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres?sslmode=require
     ```
5. In **Database** → **Extensions**, ensure `vector` is enabled (`pgvector` is pre-installed).

### Option B: Neon Serverless Postgres
1. Sign up for a free account at [neon.tech](https://neon.tech) (no credit card required).
2. Create a project (`neondb`).
3. Under **Dashboard** → **Connection Details**, toggle **Pooled connection** on.
4. Copy the connection string:
   ```text
   postgresql://[USER]:[PASSWORD]@[ENDPOINT]-pooler.neon.tech/neondb?sslmode=require
   ```

---

## 7. Database Migration Instructions

Because Vercel serverless builds are stateless and cannot connect to private internal networks during build-time, run database migrations locally from your development machine against your remote database once before deployment:

1. In your local terminal, set `DATABASE_URL` pointing to your remote Supabase or Neon database:
   ```bash
   # On Windows PowerShell:
   $env:DATABASE_URL = "postgresql://user:password@hostname:5432/dbname?sslmode=require"

   # On Linux/macOS:
   export DATABASE_URL="postgresql://user:password@hostname:5432/dbname?sslmode=require"
   ```
2. Execute the Alembic migrations:
   ```bash
   uv run alembic -c infra/alembic/alembic.ini upgrade head
   ```
3. Verify that all 11 migration versions (`0001` through `0011_sse_stream_tickets`) applied successfully. All tables, Row-Level Security policies, and indices are now active in your cloud PostgreSQL database.

---

## 8. API URL & Frontend Configuration

- **Relative Routing**: The frontend is pre-configured with relative paths (e.g. `/api/v1/requests`, `/auth/login`, `/static/...`).
- **Edge Rewrites**: `vercel.json` automatically maps all incoming browser requests:
  - `/` and `/app/*` → Served from `public/index.html` via Vercel Edge CDN.
  - `/static/*` → Cached and served by Edge CDN with `immutable` caching.
  - `/api/*`, `/auth/*`, `/health`, `/status`, `/docs` → Dispatched to `api/index.py`.
- **CORS Protection**: The API automatically permits `https://*.vercel.app` domains alongside any custom domain specified in `FRONTEND_URL` or `CORS_ORIGINS`.

---

## 9. Deployment Verification Steps

After Vercel finishes deploying:

1. **Verify Health Check**:
   Open `https://<your-project>.vercel.app/health` in your browser.
   - Status code: `200 OK` (or `503` if optional external dependencies like Neo4j/Temporal are unconfigured).
   - Expected JSON response:
     ```json
     {
       "status": "ok",
       "dependencies": {
         "postgres": { "name": "postgres", "healthy": true, "latency_ms": 42.1 }
       }
     }
     ```
2. **Verify Public Status & Telemetry Dashboard**:
   Open `https://<your-project>.vercel.app/status`.
   - Displays the operational telemetry card for the Core API Gateway, database status, and LLM Daily Budget Safeguards.
3. **Verify Interactive SPA Intake Flow**:
   Open `https://<your-project>.vercel.app/`.
   - The Omnea platform dashboard and natural language intake assistant load instantly from Edge CDN.
4. **Verify Interactive API Documentation**:
   Open `https://<your-project>.vercel.app/docs`.
   - The self-hosted Swagger UI renders under strict Content Security Policy (zero CDN script injection).

---

## 10. Troubleshooting Common Issues

### Issue 1: "Too many connections" on PostgreSQL
- **Cause**: Serverless functions scale up concurrently and can exhaust direct database connection limits.
- **Fix**: Use the Supabase **Transaction Pooler** (port `6543`) or Neon **Pooled Connection** string instead of direct connection port `5432`. Ensure `DB_SERVERLESS_POOL=true` is set in Vercel environment variables.

### Issue 2: Function Timeout (504 Gateway Timeout)
- **Cause**: Serverless invocations on Vercel Hobby are capped at 10 seconds.
- **Fix**: Long-lived background tasks (like bulk PDF batch processing or multi-minute webhooks) should be dispatched to background workers or run asynchronously. For Server-Sent Events (`/api/v1/events/stream`), the generator automatically closes cleanly after 8 seconds, prompting the frontend `EventSource` to reconnect automatically without encountering a 504.

### Issue 3: Read-Only Filesystem Error (`[Errno 30] Read-only file system`)
- **Cause**: Attempting to write files outside of `/tmp` in AWS Lambda / Vercel runtime.
- **Fix**: Already solved in the codebase: `EncryptedFileSecretsManager` and `QuarantineManager` automatically route writes to `/tmp/omnea_secrets` and `/tmp/omnea_quarantine` when running in serverless mode.

### Issue 4: Missing pgvector Extension
- **Cause**: Trying to install pgvector on an unsupported Postgres provider.
- **Fix**: Both Supabase and Neon include `pgvector` pre-installed on their free tiers. Simply run `CREATE EXTENSION IF NOT EXISTS vector;` in the SQL Editor if not enabled by default.
