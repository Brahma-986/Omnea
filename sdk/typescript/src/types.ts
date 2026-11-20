/**
 * TypeScript type definitions for Omnea API.
 */

export interface CreateRequestInput {
  vendor_name: string;
  estimated_spend: number | string;
  currency?: string;
  department?: string;
  description?: string;
  justification?: string;
  attachments?: Array<Record<string, unknown>>;
}

export interface RequestItem {
  id: string;
  status: string;
  vendor_name: string;
  estimated_spend: string;
  currency: string;
  department: string;
  description: string;
  created_at: string;
}

export interface ApprovalStep {
  id: string;
  step_name: string;
  status: string;
  assignee: string;
  agent_verdict?: Record<string, unknown>;
  human_verdict?: Record<string, unknown>;
}

export interface VendorItem {
  id: string;
  name: string;
  status: string;
  parent?: { id: string; name: string; relationship: string };
  subsidiaries?: Array<{ id: string; name: string; relationship: string }>;
  aliases?: string[];
  contracts?: Array<Record<string, unknown>>;
}

export interface WebhookEndpoint {
  id: string;
  url: string;
  description: string;
  events: string[];
  is_active: boolean;
  secret: string;
  created_at: string;
}

export interface WebhookDelivery {
  id: string;
  subscription_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  status: string;
  response_status?: number;
  attempt: number;
  success: boolean;
  duration_ms: number;
  created_at: string;
}
