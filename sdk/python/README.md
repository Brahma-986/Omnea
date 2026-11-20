# Omnea Python SDK

Official Python client library for the Omnea AI-Native Procurement Orchestration Platform.

## Quickstart (Under 20 Lines)

```python
from omnea import OmneaClient, WebhookReceiver

# Initialize client with scoped API key
client = OmneaClient(api_key="omnea_live_your_api_key_here", base_url="http://localhost:8000")

# Submit a procurement request with attachments
req = client.requests.create(
    vendor_name="Datadog, Inc.",
    estimated_spend=48000.00,
    currency="USD",
    department="Engineering",
    description="APM & log infrastructure renewal",
)
print(f"Created request: {req['id']} (Status: {req['status']})")

# Verify an incoming HMAC-SHA256 webhook
receiver = WebhookReceiver(secret="whsec_your_webhook_secret")
valid = receiver.verify_signature(
    payload_bytes=b'{"event":"request.created"}', signature_header="t=1700000000,v1=..."
)
print(f"Webhook authentic: {valid}")
```

## Features
- **Full API coverage**: Requests, Approvals, Vendors & Relationships, Documents & TPRM, ERP & Reconciliation, Webhooks, API Keys, Audit.
- **HMAC Webhook verification**: Built-in anti-replay protection with tolerance window.
- **Typed exceptions**: `AuthenticationError`, `PermissionDeniedError`, `RateLimitError`, `NotFoundError`.
