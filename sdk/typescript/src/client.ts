/**
 * Omnea TypeScript API Client.
 */

import {
  CreateRequestInput,
  RequestItem,
  VendorItem,
  WebhookEndpoint,
  WebhookDelivery,
} from "./types";

export interface OmneaClientOptions {
  apiKey: string;
  baseUrl?: string;
  tenantId?: string;
}

export class OmneaClient {
  private readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly tenantId?: string;

  constructor(options: OmneaClientOptions) {
    this.apiKey = options.apiKey.trim();
    this.baseUrl = (options.baseUrl || "http://localhost:8000").replace(/\/$/, "");
    this.tenantId = options.tenantId;
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.apiKey}`,
      "Content-Type": "application/json",
      ...(init?.headers as Record<string, string>),
    };
    if (this.tenantId) {
      headers["X-Tenant-ID"] = this.tenantId;
    }

    const res = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers,
    });

    if (!res.ok) {
      const errorText = await res.text();
      let errorJson: { error?: { message?: string }; detail?: string } = {};
      try {
        errorJson = JSON.parse(errorText);
      } catch {
        // Raw text
      }
      const msg = errorJson.error?.message || errorJson.detail || `HTTP ${res.status}: ${res.statusText}`;
      throw new Error(`Omnea API Error (${res.status}): ${msg}`);
    }

    return res.json() as Promise<T>;
  }

  public readonly requests = {
    create: (input: CreateRequestInput, idempotencyKey?: string): Promise<RequestItem> => {
      const headers = idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined;
      return this.request<RequestItem>("/api/v1/requests", {
        method: "POST",
        headers,
        body: JSON.stringify(input),
      });
    },
    get: (requestId: string): Promise<Record<string, unknown>> => {
      return this.request<Record<string, unknown>>(`/api/v1/requests/${requestId}`);
    },
    list: (limit = 50, offset = 0): Promise<{ items: RequestItem[]; total: number }> => {
      const page = Math.floor(offset / limit) + 1;
      return this.request(`/api/v1/requests?page=${page}&page_size=${limit}`);
    },
  };

  public readonly approvals = {
    listPending: (): Promise<Record<string, unknown>> => {
      return this.request("/api/v1/approvals/pending");
    },
    decide: (stepId: string, action: string, reason: string, idempotencyKey: string): Promise<Record<string, unknown>> => {
      return this.request(`/api/v1/approvals/${stepId}/decide`, {
        method: "POST",
        body: JSON.stringify({ action, reason, idempotency_key: idempotencyKey }),
      });
    },
  };

  public readonly vendors = {
    list: (): Promise<VendorItem[]> => {
      return this.request<VendorItem[]>("/api/v1/vendors");
    },
    getRelationships: (vendorId: string): Promise<Record<string, unknown>> => {
      return this.request(`/api/v1/vendors/${vendorId}/relationships`);
    },
    flag: (vendorId: string, riskType: string, reason: string): Promise<Record<string, unknown>> => {
      return this.request(`/api/v1/vendors/${vendorId}/flag`, {
        method: "POST",
        body: JSON.stringify({ risk_type: riskType, severity: "high", reason }),
      });
    },
  };

  public readonly erp = {
    getStatus: (): Promise<Record<string, unknown>> => {
      return this.request("/api/v1/erp/status");
    },
    listPurchaseOrders: (): Promise<Array<Record<string, unknown>>> => {
      return this.request("/api/v1/erp/purchase-orders");
    },
  };

  public readonly webhooks = {
    listEndpoints: (): Promise<WebhookEndpoint[]> => {
      return this.request<WebhookEndpoint[]>("/api/v1/webhooks/endpoints");
    },
    createEndpoint: (url: string, events = ["*"], description = ""): Promise<WebhookEndpoint> => {
      return this.request<WebhookEndpoint>("/api/v1/webhooks/endpoints", {
        method: "POST",
        body: JSON.stringify({ url, events, description }),
      });
    },
    listDeliveries: (limit = 50): Promise<WebhookDelivery[]> => {
      return this.request<WebhookDelivery[]>(`/api/v1/webhooks/deliveries?limit=${limit}`);
    },
    redeliver: (deliveryId: string): Promise<Record<string, unknown>> => {
      return this.request(`/api/v1/webhooks/deliveries/${deliveryId}/redeliver`, {
        method: "POST",
      });
    },
  };
}
