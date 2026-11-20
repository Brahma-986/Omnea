/**
 * Omnea API Client (api.js)
 * Enforces automated CSRF Token injection on mutating requests,
 * Bearer token auth headers, tenant boundary tagging, and fail-closed error handling.
 */

import { state } from './state.js';
import { toast } from './components/toast.js';

/**
 * Shared Query Cache with in-flight deduplication and TTL-based caching.
 * Prevents duplicate frontend network calls for repeated configurations and resource lists.
 */
export class QueryCache {
  constructor(defaultTtlMs = 30000) {
    this.defaultTtlMs = defaultTtlMs;
    this._cache = new Map(); // key -> { data, expiresAt }
    this._inFlight = new Map(); // key -> Promise
  }

  async fetch(key, fetcher, ttlMs = this.defaultTtlMs) {
    const now = Date.now();
    // 1. Check TTL cache
    const cached = this._cache.get(key);
    if (cached && cached.expiresAt > now) {
      return cached.data;
    }

    // 2. In-flight request deduplication
    if (this._inFlight.has(key)) {
      return this._inFlight.get(key);
    }

    // 3. Execute fetcher and store active promise
    const promise = (async () => {
      try {
        const data = await fetcher();
        this._cache.set(key, { data, expiresAt: Date.now() + ttlMs });
        return data;
      } finally {
        this._inFlight.delete(key);
      }
    })();

    this._inFlight.set(key, promise);
    return promise;
  }

  invalidate(keyOrPrefix) {
    if (!keyOrPrefix) {
      this.clear();
      return;
    }
    for (const key of Array.from(this._cache.keys())) {
      if (key === keyOrPrefix || key.startsWith(keyOrPrefix)) {
        this._cache.delete(key);
      }
    }
    for (const key of Array.from(this._inFlight.keys())) {
      if (key === keyOrPrefix || key.startsWith(keyOrPrefix)) {
        this._inFlight.delete(key);
      }
    }
  }

  clear() {
    this._cache.clear();
    this._inFlight.clear();
  }
}

export const queryCache = new QueryCache();

class ApiClient {
  constructor(baseUrl = '') {
    this.baseUrl = baseUrl;
  }

  async request(endpoint, options = {}) {
    const url = `${this.baseUrl}${endpoint}`;
    const headers = new Headers(options.headers || {});

    // Set JSON Content-Type if payload is present and not multipart
    if (options.body && !(options.body instanceof FormData)) {
      if (!headers.has('Content-Type')) {
        headers.set('Content-Type', 'application/json');
      }
    }

    // Inject Bearer token
    const token = state.getToken();
    if (token && !headers.has('Authorization')) {
      headers.set('Authorization', `Bearer ${token}`);
    }

    // Inject Tenant context header
    const tenantId = state.getTenantId();
    if (tenantId && !headers.has('X-Tenant-ID')) {
      headers.set('X-Tenant-ID', tenantId);
    }

    // Enforce CSRF token injection on mutating HTTP methods
    const method = (options.method || 'GET').toUpperCase();
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
      const csrfToken = state.getCsrfToken();
      if (csrfToken && !headers.has('X-CSRF-Token')) {
        headers.set('X-CSRF-Token', csrfToken);
      }
    }

    const config = {
      ...options,
      method,
      headers
    };

    let response;
    try {
      response = await fetch(url, config);
    } catch (networkError) {
      // Check if network is offline or fetch throws TypeError: Failed to fetch or server unreachable
      if (endpoint.startsWith('/api/v1/requests') && method === 'GET' && window.procureDesktop?.getOfflineRequests) {
        try {
          const cached = await window.procureDesktop.getOfflineRequests();
          if (cached) {
            return { items: cached, isOffline: true, cached: true };
          }
        } catch (cacheErr) {
          console.warn('Desktop getOfflineRequests error in api client:', cacheErr);
        }
      }
      toast.error('Network Error: Unable to reach server. Please check your connection.');
      throw networkError;
    }

    // Handle 401 Unauthorized
    if (response.status === 401) {
      // Avoid redirect loops if already on auth page
      const currentPath = window.location.hash.slice(1) || window.location.pathname;
      if (!currentPath.startsWith('/login') && !currentPath.startsWith('/signup')) {
        state.clearSession();
        toast.warning('Session expired or unauthorized. Please sign in again.');
        window.location.hash = '#/login';
      }
      const err = new Error('Unauthorized');
      err.status = 401;
      throw err;
    }

    // Handle 403 Forbidden
    if (response.status === 403) {
      const errBody = await response.json().catch(() => ({ detail: 'Access Denied: Role unauthorized' }));
      const detail = errBody.detail || 'Access Denied: Role unauthorized';
      toast.error(`Forbidden: ${detail}`);
      const err = new Error(detail);
      err.status = 403;
      throw err;
    }

    // Parse response
    const contentType = response.headers.get('content-type') || '';
    let data;
    if (contentType.includes('application/json')) {
      data = await response.json();
    } else {
      data = await response.text();
    }

    if (!response.ok) {
      const message = (data && data.detail) ? data.detail : `Request failed with status ${response.status}`;
      const err = new Error(message);
      err.status = response.status;
      err.data = data;
      throw err;
    }

    // Whenever /api/v1/requests successfully returns items, dispatch caching: window.procureDesktop.saveOfflineRequests?.(items)
    if (endpoint.startsWith('/api/v1/requests') && method === 'GET') {
      const items = Array.isArray(data) ? data : (data && Array.isArray(data.items) ? data.items : []);
      if (window.procureDesktop?.saveOfflineRequests) {
        try {
          window.procureDesktop.saveOfflineRequests(items);
        } catch (e) {
          console.warn('Desktop saveOfflineRequests error in api client:', e);
        }
      }
    }

    return data;
  }

  get(endpoint, options = {}) {
    return this.request(endpoint, { ...options, method: 'GET' });
  }

  post(endpoint, body, options = {}) {
    return this.request(endpoint, {
      ...options,
      method: 'POST',
      body: body instanceof FormData ? body : JSON.stringify(body)
    });
  }

  put(endpoint, body, options = {}) {
    return this.request(endpoint, {
      ...options,
      method: 'PUT',
      body: body instanceof FormData ? body : JSON.stringify(body)
    });
  }

  patch(endpoint, body, options = {}) {
    return this.request(endpoint, {
      ...options,
      method: 'PATCH',
      body: body instanceof FormData ? body : JSON.stringify(body)
    });
  }

  delete(endpoint, options = {}) {
    return this.request(endpoint, { ...options, method: 'DELETE' });
  }
}

export const api = new ApiClient();

// Auth Endpoints
export const authApi = {
  async signup({ organizationName, fullName, email, password }) {
    return api.post('/auth/signup', {
      organization_name: organizationName,
      full_name: fullName,
      email,
      password
    });
  },

  async login({ email, password, totpCode, tenantId }) {
    const payload = { email, password };
    if (totpCode) payload.totp_code = totpCode;
    if (tenantId) payload.tenant_id = tenantId;
    const res = await api.post('/auth/login', payload);
    if (res && res.access_token && window.procureDesktop?.saveAuthTokens) {
      try {
        await window.procureDesktop.saveAuthTokens({
          accessToken: res.access_token,
          refreshToken: res.refresh_token || '',
          tenantId: res.tenant_id || tenantId || 'default',
          email: email
        });
      } catch (e) {
        console.warn('Desktop saveAuthTokens error in login:', e);
      }
    }
    return res;
  },

  async refreshToken(refreshToken) {
    const res = await api.post('/auth/refresh', { refresh_token: refreshToken });
    if (res && res.access_token) {
      const user = state.getUser();
      const email = user?.email || '';
      const tenantId = res.tenant_id || state.getTenantId() || 'default';
      const roles = res.roles || state.getRoles();
      state.setSession(
        res.access_token,
        res.csrf_token || state.getCsrfToken() || '',
        user,
        tenantId,
        roles,
        res.refresh_token || refreshToken
      );
      if (window.procureDesktop?.saveAuthTokens) {
        try {
          await window.procureDesktop.saveAuthTokens({
            accessToken: res.access_token,
            refreshToken: res.refresh_token || refreshToken || '',
            tenantId: tenantId,
            email: email
          });
        } catch (e) {
          console.warn('Desktop saveAuthTokens error on renew:', e);
        }
      }
    }
    return res;
  },

  async getMe() {
    return api.get('/auth/me');
  },

  async forgotPassword(email) {
    return api.post('/auth/forgot-password', { email });
  },

  async resetPassword(token, newPassword) {
    return api.post('/auth/reset-password', {
      token,
      new_password: newPassword
    });
  },

  async acceptInvite(token, password, fullName) {
    return api.post('/invitations/accept', {
      token,
      password,
      full_name: fullName
    });
  },

  async logout() {
    try {
      await api.post('/auth/logout', {});
    } catch {
      // Discard server logout errors and clean local state
    } finally {
      if (window.procureDesktop?.clearAuthTokens) {
        try {
          await window.procureDesktop.clearAuthTokens();
        } catch (e) {
          console.warn('Desktop clearAuthTokens error in logout:', e);
        }
      }
      state.clearSession();
    }
  }
};

// Admin Endpoints
export const adminApi = {
  async getOverview() {
    return api.get('/api/v1/admin/overview');
  },
  async getAuditChain() {
    return api.get('/api/v1/admin/audit-chain');
  },
  async getErpHealth() {
    return api.get('/api/v1/admin/erp-health');
  },
  async getSpendCaps() {
    return api.get('/api/v1/admin/spend-caps');
  },
  async triggerErpRepair(issueId, action) {
    return api.post('/api/v1/admin/actions/erp-repair', {
      issue_id: issueId,
      action
    });
  },
  async updatePolicyConfig(config) {
    return api.post('/api/v1/admin/actions/policy-config', config);
  }
};

// Reviews Endpoints
export const reviewApi = {
  async getTasks() {
    return api.get('/api/v1/reviews/tasks');
  },
  async getTask(taskId) {
    return api.get(`/api/v1/reviews/tasks/${taskId}`);
  },
  async resolveTask(taskId, action, targetVendorId = null) {
    return api.post(`/api/v1/reviews/tasks/${taskId}/resolve`, {
      action,
      target_vendor_id: targetVendorId
    });
  }
};

// Invitations API
export const invitationsApi = {
  async createInvitation(email, role) {
    return api.post('/invitations', { email, role });
  }
};

// Requests API (Stage 3 & Stage 8 Offline Cache)
export const requestsApi = {
  async getLlmStatus() {
    return api.get('/api/v1/requests/config/llm-status');
  },
  async postIntakeChat(payload) {
    return api.post('/api/v1/requests/intake/chat', payload);
  },
  async getRequests(params = {}) {
    let query = '';
    const searchParams = new URLSearchParams();
    if (params.page) searchParams.set('page', params.page);
    if (params.pageSize) searchParams.set('page_size', params.pageSize);
    if (params.status) searchParams.set('status', params.status);
    if (params.search) searchParams.set('search', params.search);
    const qs = searchParams.toString();
    if (qs) query = `?${qs}`;

    try {
      const data = await api.get(`/api/v1/requests${query}`);
      const items = Array.isArray(data)
        ? data
        : (data && Array.isArray(data.items))
        ? data.items
        : [];

      // Whenever /api/v1/requests successfully returns items, dispatch caching:
      if (window.procureDesktop?.saveOfflineRequests) {
        try {
          window.procureDesktop.saveOfflineRequests(items);
        } catch (e) {
          console.warn('Desktop saveOfflineRequests error in requestsApi:', e);
        }
      }
      return data;
    } catch (networkError) {
      // When network is offline or fetch throws TypeError: Failed to fetch or server unreachable:
      const isOffline = !navigator.onLine ||
        networkError instanceof TypeError ||
        (networkError?.message && (
          networkError.message.includes('fetch') ||
          networkError.message.includes('Network Error') ||
          networkError.message.includes('Unable to reach server') ||
          networkError.message.includes('Failed to fetch')
        ));

      if (isOffline && window.procureDesktop?.getOfflineRequests) {
        try {
          const cached = await window.procureDesktop.getOfflineRequests();
          if (cached) {
            return { items: cached, isOffline: true, cached: true };
          }
        } catch (cacheErr) {
          console.warn('Desktop getOfflineRequests error in requestsApi:', cacheErr);
        }
      }
      throw networkError;
    }
  },
  async getRequest(id) {
    return api.get(`/api/v1/requests/${id}`);
  },
  async addComment(id, message) {
    return api.post(`/api/v1/requests/${id}/comments`, {
      comment: message,
      message
    });
  }
};

// Approvals API (Stage 3 & Stage 8 Desktop Tray/Badge Sync)
export const approvalsApi = {
  async getPending() {
    const res = await api.get('/api/v1/approvals/pending');
    try {
      const items = Array.isArray(res) ? res : (res?.pending_approvals || res?.items || []);
      const pendingCount = items.length;
      if (window.procureDesktop?.setBadgeCount) {
        window.procureDesktop.setBadgeCount(pendingCount);
      }
    } catch (e) {
      console.warn('Desktop setBadgeCount error in approvalsApi:', e);
    }
    return res;
  },
  async decide(stepId, { action, reason, idempotency_key }) {
    const res = await api.post(`/api/v1/approvals/${stepId}/decide`, {
      action,
      reason,
      idempotency_key
    });
    // Trigger badge count refresh after decision
    try {
      if (window.procureDesktop?.setBadgeCount) {
        approvalsApi.getPending().catch(() => {});
      }
    } catch {
      // ignore
    }
    return res;
  },
  async getDelegation() {
    return api.get('/api/v1/approvals/delegation');
  },
  async setDelegation(payload) {
    return api.post('/api/v1/approvals/delegation', payload);
  }
};

// Vendor Context & Review API (Stage 4)
export const vendorApi = {
  async getVendors(params = {}) {
    let query = '';
    const searchParams = new URLSearchParams();
    if (params.search) searchParams.set('search', params.search);
    if (params.status) searchParams.set('status', params.status);
    if (params.region) searchParams.set('region', params.region);
    const qs = searchParams.toString();
    if (qs) query = `?${qs}`;
    const endpoint = `/api/v1/vendors${query}`;
    return queryCache.fetch(endpoint, () => api.get(endpoint)).catch(() => null);
  },
  async getVendor(id) {
    return api.get(`/api/v1/vendors/${id}`).catch(() => null);
  },
  async getGraph(params = {}) {
    return api.get('/api/v1/vendors/graph').catch(() => null);
  },
  async mergeVendor(sourceId, targetId, reason = '') {
    const res = await api.post('/api/v1/vendors/merge', {
      source_id: sourceId,
      target_id: targetId,
      reason
    });
    queryCache.invalidate('/api/v1/vendors');
    return res;
  },
  async unmergeVendor(sourceId, targetId = null) {
    const res = await api.post(`/api/v1/vendors/${sourceId}/unmerge`, {
      source_id: sourceId,
      target_id: targetId
    });
    queryCache.invalidate('/api/v1/vendors');
    return res;
  },
  async researchVendor(vendorId, payload = {}) {
    return api.post(`/api/v1/vendors/${vendorId}/research`, payload);
  }
};

// Finance Workspace API (Stage 4)
export const financeApi = {
  async getBudgets(period = 'FY2026') {
    return api.get(`/api/v1/finance/budgets?period=${encodeURIComponent(period)}`);
  },
  async addOrUpdateBudget(payload) {
    return api.post('/api/v1/finance/budgets', payload);
  },
  async getSpendMetrics() {
    return api.get('/api/v1/finance/spend-dashboard');
  },
  async getActivePolicy() {
    const policies = await api.get('/api/v1/finance/policies').catch(() => null);
    if (Array.isArray(policies)) {
      return policies.find(p => p.is_active) || policies[0] || null;
    }
    return null;
  },
  async getPolicyHistory() {
    return api.get('/api/v1/finance/policies').catch(() => []);
  },
  async draftPolicyRule(promptText) {
    return api.post('/api/v1/finance/policies/draft', {
      prompt: promptText
    });
  },
  async confirmPolicy(policyPayload) {
    return api.post('/api/v1/finance/policies/confirm', policyPayload);
  }
};

// Legal Workspace API (Stage 4)
export const legalApi = {
  async getContracts(params = {}) {
    let query = '';
    const searchParams = new URLSearchParams();
    if (params.search) searchParams.set('search', params.search);
    if (params.status) searchParams.set('status', params.status);
    if (params.filter && params.filter !== 'All') {
      if (['active', 'expired'].includes(params.filter.toLowerCase())) {
        searchParams.set('status', params.filter.toLowerCase());
      } else if (params.filter.toLowerCase() === 'msa') {
        searchParams.set('msa_status', 'active');
      } else if (params.filter.toLowerCase() === 'dpa') {
        searchParams.set('dpa_status', 'signed');
      }
    }
    const qs = searchParams.toString();
    if (qs) query = `?${qs}`;
    return api.get(`/api/v1/legal/contracts${query}`).catch(() => []);
  },
  async getContract(id) {
    return api.get(`/api/v1/legal/contracts/${id}`);
  },
  async flagClause(contractId, payload) {
    return api.post(`/api/v1/legal/contracts/${contractId}/flag-clause`, payload);
  }
};

// Security Reviewer & TPRM API (Stage 5)
export const securityApi = {
  async uploadDocument(formData) {
    return api.post('/api/v1/security/documents/upload', formData);
  },
  async getDocuments(params = {}) {
    let query = '';
    const searchParams = new URLSearchParams();
    if (params.vendor_id) searchParams.set('vendor_id', params.vendor_id);
    if (params.include_superseded !== undefined) {
      searchParams.set('include_superseded', params.include_superseded);
    }
    const qs = searchParams.toString();
    if (qs) query = `?${qs}`;
    return api.get(`/api/v1/security/documents${query}`).catch(() => []);
  },
  async getDocument(docId) {
    return api.get(`/api/v1/security/documents/${docId}`);
  },
  async getDocumentPage(docId, pageNumber) {
    return api.get(`/api/v1/security/documents/${docId}/pages/${pageNumber}`);
  },
  async getRiskRegister() {
    return api.get('/api/v1/security/risk-register').catch(() => []);
  },
  async quarantineDocument(docId, reason) {
    const formData = new FormData();
    formData.append('reason', reason || 'Security reviewer quarantine');
    return api.post(`/api/v1/security/documents/${docId}/quarantine`, formData);
  }
};

// ERP System of Record & Saga API (Stage 6)
export const erpApi = {
  async getConfig() {
    return queryCache.fetch('/api/v1/erp/config', () => api.get('/api/v1/erp/config'));
  },
  async updateConfig(payload) {
    const res = await api.post('/api/v1/erp/config', payload);
    queryCache.invalidate('/api/v1/erp/config');
    return res;
  },
  async testConnection(connectorType = null) {
    const query = connectorType ? `?connector_type=${encodeURIComponent(connectorType)}` : '';
    return api.post(`/api/v1/erp/test-connection${query}`);
  },
  async getStatus() {
    return api.get('/api/v1/erp/status');
  },
  async getPurchaseOrders() {
    return api.get('/api/v1/erp/purchase-orders');
  },
  async getSagaExecutions() {
    return api.get('/api/v1/erp/saga-executions');
  }
};

// ERP Reconciliation & Drift Repair API (Stage 6)
export const reconciliationApi = {
  async runReconciliation() {
    return api.post('/api/v1/reconciliation/run');
  },
  async getLatestRun() {
    return api.get('/api/v1/reconciliation/latest');
  },
  async getFindings(params = {}) {
    let query = '';
    const searchParams = new URLSearchParams();
    if (params.status) searchParams.set('status', params.status);
    const qs = searchParams.toString();
    if (qs) query = `?${qs}`;
    return api.get(`/api/v1/reconciliation/findings${query}`);
  },
  async repairFinding(findingId, payload) {
    return api.post(`/api/v1/reconciliation/findings/${findingId}/repair`, payload);
  }
};

// Slack Integration API (Stage 6)
export const slackApi = {
  async getManifest() {
    return api.get('/api/v1/slack/manifest');
  },
  async getOAuthUrl() {
    return api.get('/api/v1/slack/oauth/start');
  },
  async getStatus() {
    return queryCache.fetch('/api/v1/slack/status', () => api.get('/api/v1/slack/status'));
  },
  async mapIdentity(payload) {
    const res = await api.post('/api/v1/slack/identities/map', payload);
    queryCache.invalidate('/api/v1/slack/status');
    return res;
  }
};

// Notifications & Multichannel Preferences API (Stage 6)
export const notificationsApi = {
  async getNotifications(unreadOnly = false) {
    return api.get(`/api/v1/notifications?unread_only=${unreadOnly}`);
  },
  async markAsRead(notificationId) {
    return api.post(`/api/v1/notifications/${notificationId}/read`);
  },
  async getPreferences() {
    return queryCache.fetch('/api/v1/notifications/preferences', () =>
      api.get('/api/v1/notifications/preferences')
    );
  },
  async updatePreferences(payload) {
    const res = await api.put('/api/v1/notifications/preferences', payload);
    queryCache.invalidate('/api/v1/notifications/preferences');
    return res;
  }
};

// Developer Platform API (Stage 7)
export const developerApi = {
  async listApiKeys() {
    return api.get('/api/v1/api-keys');
  },
  async createApiKey(payload) {
    return api.post('/api/v1/api-keys', payload);
  },
  async revokeApiKey(keyId) {
    return api.post(`/api/v1/api-keys/${keyId}/revoke`);
  },
  async listWebhookEndpoints() {
    return api.get('/api/v1/webhooks/endpoints');
  },
  async createWebhookEndpoint(payload) {
    return api.post('/api/v1/webhooks/endpoints', payload);
  },
  async deleteWebhookEndpoint(endpointId) {
    return api.delete(`/api/v1/webhooks/endpoints/${endpointId}`);
  },
  async listWebhookDeliveries(limit = 50) {
    return api.get(`/api/v1/webhooks/deliveries?limit=${limit}`);
  },
  async redeliverWebhook(deliveryId) {
    return api.post(`/api/v1/webhooks/deliveries/${deliveryId}/redeliver`);
  },
  async exportAudit(format = 'json') {
    return api.get(`/api/v1/audit/export?format=${format}`);
  }
};
