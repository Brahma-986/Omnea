# @omnea/sdk

Official TypeScript/JavaScript SDK for the Omnea AI-Native Procurement Orchestration Platform.

## Quickstart (Under 20 Lines)

```typescript
import { OmneaClient, verifyWebhookSignature } from "@omnea/sdk";

// Initialize client with your scoped API key
const client = new OmneaClient({ apiKey: "omnea_live_your_api_key_here" });

// Submit a procurement request
const req = await client.requests.create({
  vendor_name: "Snowflake Inc.",
  estimated_spend: 36000.00,
  department: "Data Engineering",
  description: "Enterprise data warehouse compute credits",
});
console.log(`Created request: ${req.id} (Status: ${req.status})`);

// Verify an incoming HMAC-SHA256 signed webhook
const isValid = verifyWebhookSignature({
  payload: '{"event":"request.created"}',
  signatureHeader: "t=1700000000,v1=...",
  secret: "whsec_your_secret",
});
console.log(`Webhook authentic: ${isValid}`);
```
