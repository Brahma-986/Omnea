/**
 * Developer Platform & API Workspace View (developer_views.js)
 * Manages API keys (scopes, rate limits, hashed storage, revocation),
 * outbound webhook endpoints, delivery logs, manual redelivery,
 * and developer documentation with copy-paste curl examples.
 */

import { state, escapeHtml } from '../state.js';
import { toast } from '../components/toast.js';
import { developerApi } from '../api.js';

export async function renderDeveloperPlatformView(container) {
  container.innerHTML = '';

  const header = document.createElement('div');
  header.className = 'page-header';
  header.innerHTML = `
    <div>
      <h1 class="page-title">Developer Platform & API</h1>
      <p class="page-description">Generate scoped API keys, configure HMAC-SHA256 outbound webhooks, and integrate SDKs.</p>
    </div>
    <div style="display: flex; gap: var(--space-2); flex-wrap: wrap;">
      <a href="/docs" target="_blank" class="btn btn-primary" id="btn-open-swagger">Open Swagger UI (/docs)</a>
      <a href="#/app/admin/org" class="btn btn-secondary">Organization Settings</a>
    </div>
  `;
  container.appendChild(header);

  // Tab Navigation
  const tabContainer = document.createElement('div');
  tabContainer.className = 'tabs-container';
  tabContainer.style.marginBottom = 'var(--space-6)';
  tabContainer.innerHTML = `
    <div class="tabs-nav" role="tablist">
      <button class="tab-btn is-active" data-tab="api-keys" role="tab">API Keys</button>
      <button class="tab-btn" data-tab="webhooks" role="tab">Outbound Webhooks</button>
      <button class="tab-btn" data-tab="curl-docs" role="tab">cURL & SDK Docs</button>
    </div>
    <div id="dev-tab-content" style="margin-top: var(--space-6);"></div>
  `;
  container.appendChild(tabContainer);

  const tabContent = tabContainer.querySelector('#dev-tab-content');
  const tabButtons = tabContainer.querySelectorAll('.tab-btn');

  function switchTab(tabKey) {
    tabButtons.forEach(btn => {
      btn.classList.toggle('is-active', btn.getAttribute('data-tab') === tabKey);
    });
    if (tabKey === 'api-keys') {
      _renderApiKeysTab(tabContent);
    } else if (tabKey === 'webhooks') {
      _renderWebhooksTab(tabContent);
    } else if (tabKey === 'curl-docs') {
      _renderCurlDocsTab(tabContent);
    }
  }

  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      switchTab(btn.getAttribute('data-tab'));
    });
  });

  // Default to API keys
  switchTab('api-keys');
}

async function _renderApiKeysTab(container) {
  container.innerHTML = `
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-4);">
      <div>
        <h3 style="margin: 0; font-size: var(--text-base); font-weight: var(--font-semibold);">Active API Keys</h3>
        <p style="margin: 2px 0 0; font-size: var(--text-xs); color: var(--text-secondary);">
          Scoped programmatic tokens. Stored as SHA-256 hashes and enforced by PostgreSQL RLS.
        </p>
      </div>
      <button class="btn btn-primary" id="btn-create-api-key">+ Generate API Key</button>
    </div>
    <div id="api-keys-table-container">
      <div style="padding: var(--space-6); text-align: center; color: var(--text-muted);">Loading API keys...</div>
    </div>
  `;

  const tableContainer = container.querySelector('#api-keys-table-container');
  const createBtn = container.querySelector('#btn-create-api-key');

  createBtn.addEventListener('click', () => {
    _showCreateApiKeyModal(() => _renderApiKeysTab(container));
  });

  try {
    const keys = await developerApi.listApiKeys();
    if (!keys || keys.length === 0) {
      tableContainer.innerHTML = `
        <div class="empty-state" style="padding: var(--space-8); border: 1px dashed var(--border-color); border-radius: var(--radius-md);">
          <p style="color: var(--text-secondary); margin-bottom: var(--space-4);">No API keys generated yet.</p>
          <button class="btn btn-primary btn-sm" id="btn-create-first-key">Generate First Key</button>
        </div>
      `;
      tableContainer.querySelector('#btn-create-first-key')?.addEventListener('click', () => {
        _showCreateApiKeyModal(() => _renderApiKeysTab(container));
      });
      return;
    }

    let rowsHtml = '';
    keys.forEach(k => {
      const statusBadge = k.is_active
        ? '<span class="badge badge-success">Active</span>'
        : '<span class="badge badge-danger">Revoked</span>';

      const scopesBadges = (k.scopes || [])
        .map(s => `<span class="badge badge-neutral" style="font-size: 10px; margin: 1px;">${escapeHtml(s)}</span>`)
        .join(' ');

      const revokeAction = k.is_active
        ? `<button class="btn btn-danger btn-sm btn-revoke-key" data-id="${k.id}">Revoke</button>`
        : '<span style="color: var(--text-muted); font-size: var(--text-xs);">Revoked</span>';

      rowsHtml += `
        <tr>
          <td><strong>${escapeHtml(k.name)}</strong></td>
          <td><code>${escapeHtml(k.key_prefix)}...</code></td>
          <td style="max-width: 250px;">${scopesBadges}</td>
          <td>${escapeHtml(String(k.rate_limit_per_minute))} req/min</td>
          <td>${statusBadge}</td>
          <td style="font-size: var(--text-xs); color: var(--text-secondary);">${k.last_used_at ? new Date(k.last_used_at).toLocaleDateString() : 'Never'}</td>
          <td>${revokeAction}</td>
        </tr>
      `;
    });

    tableContainer.innerHTML = `
      <div class="table-card">
        <table class="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Key Prefix</th>
              <th>Granted Scopes</th>
              <th>Rate Limit</th>
              <th>Status</th>
              <th>Last Used</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>${rowsHtml}</tbody>
        </table>
      </div>
    `;

    tableContainer.querySelectorAll('.btn-revoke-key').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        const id = e.currentTarget.getAttribute('data-id');
        if (confirm('Revoke this API key immediately? This will permanently disable client access.')) {
          try {
            await developerApi.revokeApiKey(id);
            toast.success('API key revoked.');
            _renderApiKeysTab(container);
          } catch (err) {
            toast.error('Failed to revoke API key.');
          }
        }
      });
    });

  } catch (err) {
    tableContainer.innerHTML = `<div class="toast-error" style="padding: var(--space-4);">Failed to load API keys: ${escapeHtml(err.message)}</div>`;
  }
}

function _showCreateApiKeyModal(onSuccess) {
  const modal = document.createElement('div');
  modal.className = 'modal-backdrop is-open';
  modal.innerHTML = `
    <div class="modal-dialog" style="max-width: 550px;">
      <div class="modal-header">
        <h3 class="modal-title">Generate Scoped API Key</h3>
        <button type="button" class="btn-close" id="btn-close-modal">✕</button>
      </div>
      <div class="modal-body" id="modal-key-content">
        <form id="create-key-form">
          <div class="form-group">
            <label class="form-label">Key Name</label>
            <input type="text" id="new-key-name" class="form-input" placeholder="e.g. Production CI/CD Key" required minlength="2">
          </div>
          <div class="form-group">
            <label class="form-label">Rate Limit Ceiling (req/min)</label>
            <input type="number" id="new-key-ratelimit" class="form-input" value="60" min="1" max="1000">
          </div>
          <div class="form-group">
            <label class="form-label">Granted Permissions & Scopes</label>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-2); margin-top: var(--space-2);">
              <label><input type="checkbox" name="scope" value="requests:read" checked> requests:read</label>
              <label><input type="checkbox" name="scope" value="requests:create" checked> requests:create</label>
              <label><input type="checkbox" name="scope" value="approvals:read" checked> approvals:read</label>
              <label><input type="checkbox" name="scope" value="approvals:decide"> approvals:decide</label>
              <label><input type="checkbox" name="scope" value="vendors:read" checked> vendors:read</label>
              <label><input type="checkbox" name="scope" value="vendors:manage"> vendors:manage</label>
              <label><input type="checkbox" name="scope" value="documents:read"> documents:read</label>
              <label><input type="checkbox" name="scope" value="erp:read"> erp:read</label>
              <label><input type="checkbox" name="scope" value="audit:export"> audit:export</label>
              <label><input type="checkbox" name="scope" value="webhooks:manage"> webhooks:manage</label>
            </div>
          </div>
          <div style="display: flex; justify-content: flex-end; gap: var(--space-2); margin-top: var(--space-6);">
            <button type="button" class="btn btn-secondary" id="btn-cancel-modal">Cancel</button>
            <button type="submit" class="btn btn-primary" id="btn-submit-key">Generate Key</button>
          </div>
        </form>
      </div>
    </div>
  `;

  document.body.appendChild(modal);

  const close = () => modal.remove();
  modal.querySelector('#btn-close-modal').addEventListener('click', close);
  modal.querySelector('#btn-cancel-modal').addEventListener('click', close);

  modal.querySelector('#create-key-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = modal.querySelector('#new-key-name').value.trim();
    const rateLimit = parseInt(modal.querySelector('#new-key-ratelimit').value, 10);
    const checkedScopes = Array.from(modal.querySelectorAll('input[name="scope"]:checked')).map(cb => cb.value);

    try {
      const res = await developerApi.createApiKey({
        name,
        scopes: checkedScopes,
        rate_limit_per_minute: rateLimit,
      });

      // Show generated raw key once
      const content = modal.querySelector('#modal-key-content');
      content.innerHTML = `
        <div style="background: var(--bg-subtle); padding: var(--space-4); border-radius: var(--radius-md); border-left: 4px solid var(--success);">
          <strong style="color: var(--success); font-size: var(--text-sm);">API Key Successfully Generated!</strong>
          <p style="font-size: var(--text-xs); color: var(--text-secondary); margin: var(--space-2) 0;">
            Copy this secret now. It is stored using secure SHA-256 hashing and cannot be displayed again.
          </p>
          <div style="display: flex; gap: var(--space-2); align-items: center; margin-top: var(--space-3);">
            <input type="text" id="raw-key-input" class="form-input" value="${escapeHtml(res.raw_key)}" readonly style="font-family: monospace; font-size: 13px;">
            <button class="btn btn-primary btn-sm" id="btn-copy-raw-key">Copy</button>
          </div>
        </div>
        <div style="display: flex; justify-content: flex-end; margin-top: var(--space-6);">
          <button type="button" class="btn btn-primary" id="btn-done-modal">Done</button>
        </div>
      `;

      content.querySelector('#btn-copy-raw-key').addEventListener('click', () => {
        navigator.clipboard.writeText(res.raw_key);
        toast.success('API key copied to clipboard.');
      });

      content.querySelector('#btn-done-modal').addEventListener('click', () => {
        close();
        if (typeof onSuccess === 'function') onSuccess();
      });

    } catch (err) {
      toast.error(`Error: ${err.message}`);
    }
  });
}

async function _renderWebhooksTab(container) {
  container.innerHTML = `
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-4);">
      <div>
        <h3 style="margin: 0; font-size: var(--text-base); font-weight: var(--font-semibold);">Outbound Webhooks</h3>
        <p style="margin: 2px 0 0; font-size: var(--text-xs); color: var(--text-secondary);">
          Cryptographically signed HTTP POST notifications with HMAC-SHA256 and exponential backoff retry.
        </p>
      </div>
      <button class="btn btn-primary" id="btn-add-webhook-endpoint">+ Add Webhook Endpoint</button>
    </div>

    <!-- Endpoints Section -->
    <div id="endpoints-container" style="margin-bottom: var(--space-6);"></div>

    <!-- Delivery Logs Section -->
    <h3 style="margin: var(--space-6) 0 var(--space-2); font-size: var(--text-base); font-weight: var(--font-semibold);">Delivery Logs</h3>
    <div id="deliveries-container"></div>
  `;

  const endpointsContainer = container.querySelector('#endpoints-container');
  const deliveriesContainer = container.querySelector('#deliveries-container');
  const addBtn = container.querySelector('#btn-add-webhook-endpoint');

  addBtn.addEventListener('click', () => {
    _showAddWebhookModal(() => _renderWebhooksTab(container));
  });

  // 1. Fetch Endpoints
  try {
    const endpoints = await developerApi.listWebhookEndpoints();
    if (!endpoints || endpoints.length === 0) {
      endpointsContainer.innerHTML = `
        <div class="empty-state" style="padding: var(--space-6); border: 1px dashed var(--border-color); border-radius: var(--radius-md);">
          <p style="color: var(--text-secondary); margin: 0;">No webhook endpoints registered.</p>
        </div>
      `;
    } else {
      let rowsHtml = '';
      endpoints.forEach(ep => {
        const eventsBadges = (ep.events || [])
          .map(e => `<span class="badge badge-primary" style="font-size: 10px; margin: 1px;">${escapeHtml(e)}</span>`)
          .join(' ');

        rowsHtml += `
          <tr>
            <td><code>${escapeHtml(ep.url)}</code></td>
            <td>${eventsBadges}</td>
            <td><code>${escapeHtml(ep.secret.slice(0, 12))}...</code></td>
            <td><span class="badge badge-success">Active</span></td>
            <td><button class="btn btn-danger btn-sm btn-del-endpoint" data-id="${ep.id}">Delete</button></td>
          </tr>
        `;
      });

      endpointsContainer.innerHTML = `
        <div class="table-card">
          <table class="data-table">
            <thead>
              <tr>
                <th>Destination URL</th>
                <th>Subscribed Events</th>
                <th>Signing Secret</th>
                <th>Status</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>${rowsHtml}</tbody>
          </table>
        </div>
      `;

      endpointsContainer.querySelectorAll('.btn-del-endpoint').forEach(btn => {
        btn.addEventListener('click', async (e) => {
          const id = e.currentTarget.getAttribute('data-id');
          if (confirm('Delete this webhook endpoint?')) {
            await developerApi.deleteWebhookEndpoint(id);
            toast.success('Endpoint removed.');
            _renderWebhooksTab(container);
          }
        });
      });
    }
  } catch (err) {
    endpointsContainer.innerHTML = `<div class="toast-error">Failed to load endpoints: ${escapeHtml(err.message)}</div>`;
  }

  // 2. Fetch Deliveries
  try {
    const deliveries = await developerApi.listWebhookDeliveries(25);
    if (!deliveries || deliveries.length === 0) {
      deliveriesContainer.innerHTML = `<div style="padding: var(--space-4); color: var(--text-muted); font-size: var(--text-xs);">No delivery attempts recorded yet.</div>`;
    } else {
      let delRows = '';
      deliveries.forEach(d => {
        const statusBadge = d.success
          ? '<span class="badge badge-success">200 OK</span>'
          : `<span class="badge badge-danger">Failed (${d.response_status || 'Err'})</span>`;

        delRows += `
          <tr>
            <td><code>${escapeHtml(d.id.slice(0, 8))}...</code></td>
            <td><strong>${escapeHtml(d.event_type)}</strong></td>
            <td>${statusBadge}</td>
            <td>Attempt ${escapeHtml(String(d.attempt))}</td>
            <td>${escapeHtml(String(d.duration_ms))}ms</td>
            <td style="font-size: var(--text-xs); color: var(--text-secondary);">${new Date(d.created_at).toLocaleTimeString()}</td>
            <td>
              <button class="btn btn-secondary btn-sm btn-redeliver" data-id="${d.id}">Redeliver</button>
            </td>
          </tr>
        `;
      });

      deliveriesContainer.innerHTML = `
        <div class="table-card">
          <table class="data-table">
            <thead>
              <tr>
                <th>Delivery ID</th>
                <th>Event</th>
                <th>Status</th>
                <th>Attempt</th>
                <th>Duration</th>
                <th>Time</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>${delRows}</tbody>
          </table>
        </div>
      `;

      deliveriesContainer.querySelectorAll('.btn-redeliver').forEach(btn => {
        btn.addEventListener('click', async (e) => {
          const id = e.currentTarget.getAttribute('data-id');
          btn.textContent = 'Sending...';
          try {
            await developerApi.redeliverWebhook(id);
            toast.success('Webhook redelivered.');
            _renderWebhooksTab(container);
          } catch (err) {
            toast.error('Redelivery failed.');
            btn.textContent = 'Redeliver';
          }
        });
      });
    }
  } catch (err) {
    deliveriesContainer.innerHTML = `<div class="toast-error">Failed to load delivery logs: ${escapeHtml(err.message)}</div>`;
  }
}

function _showAddWebhookModal(onSuccess) {
  const modal = document.createElement('div');
  modal.className = 'modal-backdrop is-open';
  modal.innerHTML = `
    <div class="modal-dialog" style="max-width: 550px;">
      <div class="modal-header">
        <h3 class="modal-title">Register Webhook Endpoint</h3>
        <button type="button" class="btn-close" id="btn-close-webhook-modal">✕</button>
      </div>
      <div class="modal-body">
        <form id="create-webhook-form">
          <div class="form-group">
            <label class="form-label">Destination URL</label>
            <input type="url" id="webhook-url-input" class="form-input" placeholder="https://api.yourdomain.com/webhooks/omnea" required>
            <div class="form-help">Must accept HTTP POST requests with X-Omnea-Signature HMAC headers.</div>
          </div>
          <div class="form-group">
            <label class="form-label">Description / Label</label>
            <input type="text" id="webhook-desc-input" class="form-input" placeholder="e.g. ERP Ingestion Gateway">
          </div>
          <div class="form-group">
            <label class="form-label">Subscribed Events</label>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-2); margin-top: var(--space-2);">
              <label><input type="checkbox" name="whevent" value="*" checked> * (All Events)</label>
              <label><input type="checkbox" name="whevent" value="request.created"> request.created</label>
              <label><input type="checkbox" name="whevent" value="request.approved"> request.approved</label>
              <label><input type="checkbox" name="whevent" value="request.rejected"> request.rejected</label>
              <label><input type="checkbox" name="whevent" value="vendor.flagged"> vendor.flagged</label>
              <label><input type="checkbox" name="whevent" value="document.assessed"> document.assessed</label>
              <label><input type="checkbox" name="whevent" value="erp.synced"> erp.synced</label>
            </div>
          </div>
          <div style="display: flex; justify-content: flex-end; gap: var(--space-2); margin-top: var(--space-6);">
            <button type="button" class="btn btn-secondary" id="btn-cancel-webhook-modal">Cancel</button>
            <button type="submit" class="btn btn-primary">Create Endpoint</button>
          </div>
        </form>
      </div>
    </div>
  `;

  document.body.appendChild(modal);

  const close = () => modal.remove();
  modal.querySelector('#btn-close-webhook-modal').addEventListener('click', close);
  modal.querySelector('#btn-cancel-webhook-modal').addEventListener('click', close);

  modal.querySelector('#create-webhook-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const url = modal.querySelector('#webhook-url-input').value.trim();
    const description = modal.querySelector('#webhook-desc-input').value.trim();
    const events = Array.from(modal.querySelectorAll('input[name="whevent"]:checked')).map(cb => cb.value);

    try {
      await developerApi.createWebhookEndpoint({ url, description, events });
      toast.success('Webhook endpoint registered.');
      close();
      if (typeof onSuccess === 'function') onSuccess();
    } catch (err) {
      toast.error(`Failed to create endpoint: ${err.message}`);
    }
  });
}

function _renderCurlDocsTab(container) {
  const curlExamples = [
    {
      title: '1. Submit Procurement Request (Text + Attachments)',
      method: 'POST',
      path: '/api/v1/requests',
      curl: `curl -X POST "http://localhost:8000/api/v1/requests" \\
  -H "Authorization: Bearer omnea_live_YOUR_KEY" \\
  -H "Content-Type: application/json" \\
  -H "Idempotency-Key: req_idem_$(date +%s)" \\
  -d '{
    "vendor_name": "Datadog, Inc.",
    "estimated_spend": "48000.00",
    "currency": "USD",
    "department": "Engineering",
    "description": "Annual APM & telemetry renewal",
    "attachments": []
  }'`
    },
    {
      title: '2. Get Request Details & Approver Timeline',
      method: 'GET',
      path: '/api/v1/requests/{id}',
      curl: `curl -X GET "http://localhost:8000/api/v1/requests/REQ_UUID_HERE" \\
  -H "Authorization: Bearer omnea_live_YOUR_KEY"`
    },
    {
      title: '3. List Pending Approvals Awaiting Decision',
      method: 'GET',
      path: '/api/v1/approvals/pending',
      curl: `curl -X GET "http://localhost:8000/api/v1/approvals/pending" \\
  -H "Authorization: Bearer omnea_live_YOUR_KEY"`
    },
    {
      title: '4. Submit Approver Decision (Idempotent)',
      method: 'POST',
      path: '/api/v1/approvals/{step_id}/decide',
      curl: `curl -X POST "http://localhost:8000/api/v1/approvals/STEP_UUID_HERE/decide" \\
  -H "Authorization: Bearer omnea_live_YOUR_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "action": "approve",
    "reason": "Budget validated and quotes verified",
    "idempotency_key": "decide_$(date +%s)"
  }'`
    },
    {
      title: '5. Query Vendor Corporate Relationships & Spend',
      method: 'GET',
      path: '/api/v1/vendors/{id}/relationships',
      curl: `curl -X GET "http://localhost:8000/api/v1/vendors/VENDOR_UUID_HERE/relationships" \\
  -H "Authorization: Bearer omnea_live_YOUR_KEY"`
    },
    {
      title: '6. Inspect Document Verified TPRM Findings',
      method: 'GET',
      path: '/api/v1/documents/{id}/assessment',
      curl: `curl -X GET "http://localhost:8000/api/v1/documents/DOC_UUID_HERE/assessment" \\
  -H "Authorization: Bearer omnea_live_YOUR_KEY"`
    },
    {
      title: '7. Export Immutable Merkle Audit Log (JSON or CSV)',
      method: 'GET',
      path: '/api/v1/audit/export?format=json',
      curl: `curl -X GET "http://localhost:8000/api/v1/audit/export?format=json&limit=500" \\
  -H "Authorization: Bearer omnea_live_YOUR_KEY"`
    }
  ];

  let cardsHtml = '';
  curlExamples.forEach((ex, idx) => {
    cardsHtml += `
      <div class="table-card" style="margin-bottom: var(--space-4); padding: var(--space-4);">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-2);">
          <strong style="font-size: var(--text-sm);">${escapeHtml(ex.title)}</strong>
          <span class="badge badge-primary">${escapeHtml(ex.method)} ${escapeHtml(ex.path)}</span>
        </div>
        <div style="position: relative;">
          <pre style="background: var(--bg-surface); padding: var(--space-3); border-radius: var(--radius-sm); font-size: 12px; overflow-x: auto; margin: 0;"><code>${escapeHtml(ex.curl)}</code></pre>
          <button class="btn btn-secondary btn-sm btn-copy-curl" data-index="${idx}" style="position: absolute; top: 6px; right: 6px;">Copy cURL</button>
        </div>
      </div>
    `;
  });

  container.innerHTML = `
    <div style="margin-bottom: var(--space-6);">
      <h3 style="margin: 0 0 var(--space-2); font-size: var(--text-base); font-weight: var(--font-semibold);">
        cURL Code Examples & Quickstart Reference
      </h3>
      <p style="font-size: var(--text-xs); color: var(--text-secondary); margin: 0 0 var(--space-4);">
        All mutating endpoints support the <code>Idempotency-Key</code> header. Pass your scoped key in the <code>Authorization: Bearer &lt;key&gt;</code> header.
      </p>
      ${cardsHtml}
    </div>
  `;

  container.querySelectorAll('.btn-copy-curl').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const idx = parseInt(e.currentTarget.getAttribute('data-index'), 10);
      const textToCopy = curlExamples[idx].curl;
      navigator.clipboard.writeText(textToCopy);
      toast.success('cURL command copied to clipboard.');
    });
  });
}
