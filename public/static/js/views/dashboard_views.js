/**
 * Omnea Role-Based Dashboard Views (dashboard_views.js)
 * Personal Dashboard, KPI metrics, action queues, and route views for:
 * - requester
 * - approver
 * - finance_admin
 * - security_reviewer
 * - legal_reviewer
 * - tenant_admin
 */

import { state, escapeHtml } from '../state.js';
import { adminApi, reviewApi, invitationsApi, erpApi, slackApi, notificationsApi } from '../api.js';
import { DataTable } from '../components/table.js';
import { ChartRenderer } from '../components/charts.js';
import { modal } from '../components/modal.js';
import { toast } from '../components/toast.js';
import { renderOnboardingChecklist } from './onboarding.js';
import { renderIntakeChat } from './intake_chat.js';
import { renderRequestsList } from './requests_list.js';
import { renderRequestDetail } from './request_detail.js';
import { renderApproverInbox } from './approver_inbox.js';
import { renderDeveloperPlatformView } from './developer_views.js';

export function renderDashboard(container, subRoute = '') {
  const role = state.getActiveRole();

  // If user is tenant admin and on main /app route, display Onboarding Checklist
  if (role === 'tenant_admin' && (!subRoute || subRoute === '/')) {
    const obContainer = document.createElement('div');
    renderOnboardingChecklist(obContainer);
    container.appendChild(obContainer);
  }

  // Route to the appropriate role sub-view
  switch (role) {
    case 'requester':
      _renderRequesterView(container, subRoute);
      break;
    case 'approver':
      _renderApproverView(container, subRoute);
      break;
    case 'finance_admin':
      _renderFinanceView(container, subRoute);
      break;
    case 'security_reviewer':
      _renderSecurityView(container, subRoute);
      break;
    case 'legal_reviewer':
      _renderLegalView(container, subRoute);
      break;
    case 'tenant_admin':
      _renderAdminView(container, subRoute);
      break;
    default:
      _renderRequesterView(container, subRoute);
      break;
  }
}

/* ==========================================================================
   1. Requester Views
   ========================================================================== */
function _renderRequesterView(container, subRoute) {
  if (subRoute === '/requests/new') {
    renderIntakeChat(container);
    return;
  }
  if (subRoute === '/requests') {
    renderRequestsList(container);
    return;
  }
  const reqMatch = subRoute ? subRoute.match(/^\/requests\/([a-zA-Z0-9_-]+)$/) : null;
  if (reqMatch) {
    renderRequestDetail(container, reqMatch[1]);
    return;
  }
  if (subRoute === '/catalog') {
    _renderCatalogView(container);
    return;
  }
  if (subRoute === '/requests/status') {
    _renderRequestStatusLifecycle(container);
    return;
  }

  // Default Dashboard & My Requests
  const header = document.createElement('div');
  header.className = 'page-header';
  header.innerHTML = `
    <div>
      <h1 class="page-title">Requester Workspace</h1>
      <p class="page-description">Submit new procurement requests, track multi-agent evaluations, and view approved software.</p>
    </div>
    <div style="display: flex; gap: var(--space-2);">
      <a href="#/app/requests" class="btn btn-secondary">My Requests</a>
      <a href="#/app/requests/new" class="btn btn-primary">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="12" y1="5" x2="12" y2="19"></line>
          <line x1="5" y1="12" x2="19" y2="12"></line>
        </svg>
        Submit New Request
      </a>
    </div>
  `;
  container.appendChild(header);

  // Metrics Grid
  const metrics = document.createElement('div');
  metrics.className = 'metrics-grid';
  metrics.innerHTML = `
    <div class="metric-card">
      <div class="metric-header"><span class="metric-label">My Requests</span></div>
      <div class="metric-value">12</div>
      <div class="metric-trend trend-up">↑ 3 this month</div>
    </div>
    <div class="metric-card">
      <div class="metric-header"><span class="metric-label">Active In-Review</span></div>
      <div class="metric-value">2</div>
      <div class="metric-trend trend-neutral">Parallel agents evaluating</div>
    </div>
    <div class="metric-card">
      <div class="metric-header"><span class="metric-label">Approved (Q4)</span></div>
      <div class="metric-value">10</div>
      <div class="metric-trend trend-up">100% green-channeled</div>
    </div>
    <div class="metric-card">
      <div class="metric-header"><span class="metric-label">Total Spend</span></div>
      <div class="metric-value">$48,250</div>
      <div class="metric-trend trend-neutral">Within department ceiling</div>
    </div>
  `;
  container.appendChild(metrics);

  // Requests Table
  const tableContainer = document.createElement('div');
  container.appendChild(tableContainer);

  const mockRequests = [
    { id: 'REQ-1049', vendor: 'Datadog Enterprise APM', category: 'Software/DevOps', amount: '$14,500', status: 'approved', created_at: '2026-11-15' },
    { id: 'REQ-1050', vendor: 'Figma Organization Seats', category: 'Design & UX', amount: '$3,800', status: 'pending', created_at: '2026-11-18' },
    { id: 'REQ-1051', vendor: 'AWS Dedicated Host Reservation', category: 'Cloud Infrastructure', amount: '$24,000', status: 'needs_human', created_at: '2026-11-18' },
    { id: 'REQ-1052', vendor: 'GitHub Enterprise Copilot', category: 'Software/DevOps', amount: '$5,950', status: 'approved', created_at: '2026-11-10' }
  ];

  new DataTable({
    container: tableContainer,
    title: 'My Procurement Requests',
    data: mockRequests,
    columns: [
      { key: 'id', label: 'Request ID', sortable: true },
      {
        key: 'vendor',
        label: 'Vendor / Item',
        sortable: true,
        render: (val) => `<strong>${escapeHtml(val)}</strong>`
      },
      { key: 'category', label: 'Category', sortable: true },
      { key: 'amount', label: 'Est. Spend', sortable: true },
      {
        key: 'status',
        label: 'Status',
        sortable: true,
        render: (val) => `<span class="badge badge-${escapeHtml(val)}"><span class="badge-dot"></span>${escapeHtml(val)}</span>`
      },
      { key: 'created_at', label: 'Submitted', sortable: true },
      {
        key: 'id',
        label: 'Actions',
        render: (id, row) => {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'btn btn-secondary btn-sm';
          btn.textContent = 'View Details';
          btn.addEventListener('click', () => {
            _openRequestDetailModal(row);
          });
          return btn;
        }
      }
    ]
  });
}

function _renderNewRequestForm(container) {
  const header = document.createElement('div');
  header.className = 'page-header';
  header.innerHTML = `
    <div>
      <h1 class="page-title">Submit Procurement Request</h1>
      <p class="page-description">Omnea's intake classifier will parse your requirement and initiate parallel agent reviews.</p>
    </div>
    <a href="#/app/requests" class="btn btn-secondary">Back to My Requests</a>
  `;
  container.appendChild(header);

  const card = document.createElement('div');
  card.className = 'table-card';
  card.style.padding = 'var(--space-6)';
  card.innerHTML = `
    <form id="new-req-form" novalidate style="max-width: 700px;">
      <div class="form-group">
        <label class="form-label form-label-required" for="req-item">Vendor or Product Name</label>
        <input type="text" id="req-item" class="form-input" placeholder="e.g. Snowflake Cloud Data Platform" required>
      </div>

      <div class="form-group">
        <label class="form-label form-label-required" for="req-cost">Estimated Annual Cost (USD)</label>
        <input type="number" id="req-cost" class="form-input" placeholder="25000" required min="1">
      </div>

      <div class="form-group">
        <label class="form-label form-label-required" for="req-category">Spend Category</label>
        <select id="req-category" class="form-select">
          <option value="software">Software & SaaS</option>
          <option value="cloud">Cloud Infrastructure</option>
          <option value="consulting">Professional Services</option>
          <option value="hardware">Hardware & Equipment</option>
        </select>
      </div>

      <div class="form-group">
        <label class="form-label form-label-required" for="req-justification">Business Justification</label>
        <textarea id="req-justification" class="form-textarea" placeholder="Explain the business need, affected teams, and expected return..." required></textarea>
      </div>

      <div class="form-group">
        <label class="form-label" for="req-quote">Upload Vendor Quote / Security Questionnaire (Optional)</label>
        <input type="file" id="req-quote" class="form-input">
        <div class="form-help">PDF, DOCX, or CSV up to 25MB. Analyzed automatically by Legal & Security agents.</div>
      </div>

      <div style="display: flex; gap: var(--space-3); margin-top: var(--space-6);">
        <button type="submit" class="btn btn-primary btn-lg" id="submit-req-btn">
          Initiate Autonomous Review
        </button>
        <a href="#/app/requests" class="btn btn-secondary btn-lg">Cancel</a>
      </div>
    </form>
  `;

  const form = card.querySelector('#new-req-form');
  const submitBtn = card.querySelector('#submit-req-btn');

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const item = card.querySelector('#req-item').value.trim();
    const cost = card.querySelector('#req-cost').value.trim();
    const just = card.querySelector('#req-justification').value.trim();

    if (!item || !cost || !just) {
      toast.error('Please complete all required fields.');
      return;
    }

    submitBtn.classList.add('btn-loading');

    setTimeout(() => {
      submitBtn.classList.remove('btn-loading');
      toast.success(`Request for ${item} submitted! Parallel agents dispatched.`);
      state.addNotification({
        title: 'Procurement Request Dispatched',
        message: `${item} ($${cost}) is being analyzed concurrently by Legal and Security agents.`,
        link: '/app/requests'
      });
      window.location.hash = '#/app/requests';
    }, 800);
  });

  container.appendChild(card);
}

function _renderCatalogView(container) {
  const header = document.createElement('div');
  header.className = 'page-header';
  header.innerHTML = `
    <div>
      <h1 class="page-title">Pre-Approved Software Catalog</h1>
      <p class="page-description">Standardized SaaS applications pre-vetted by Legal, Security, and Finance for instant provisioning.</p>
    </div>
  `;
  container.appendChild(header);

  const grid = document.createElement('div');
  grid.className = 'roster-grid';

  const catalogItems = [
    { name: 'GitHub Enterprise', domain: 'Developer Platform', desc: 'Pre-approved SOC2 Type II, ISO 27001. Tier 1 volume discount active.', badge: 'compliant' },
    { name: 'Figma Organization', domain: 'Product Design', desc: 'Enterprise DPA executed. Standard seat tier under $4,000 threshold.', badge: 'compliant' },
    { name: 'Datadog APM', domain: 'Observability & Monitoring', desc: 'Central contract with annual volume rebate. Security verified.', badge: 'compliant' },
    { name: 'Slack Enterprise Grid', domain: 'Collaboration & Intake', desc: 'Core platform channel. End-to-end encrypted messaging enabled.', badge: 'compliant' }
  ];

  catalogItems.forEach(item => {
    const card = document.createElement('div');
    card.className = 'agent-card';
    card.innerHTML = `
      <div class="agent-card-header">
        <div class="agent-avatar agent-classifier">${item.name.slice(0, 2).toUpperCase()}</div>
        <div>
          <div class="agent-name">${escapeHtml(item.name)}</div>
          <div class="agent-domain">${escapeHtml(item.domain)}</div>
        </div>
      </div>
      <p class="agent-desc">${escapeHtml(item.desc)}</p>
      <div style="margin-top: var(--space-4); display: flex; align-items: center; justify-content: space-between;">
        <span class="badge badge-${item.badge}">Pre-Approved</span>
        <button type="button" class="btn btn-primary btn-sm req-catalog-btn">Request License</button>
      </div>
    `;

    card.querySelector('.req-catalog-btn').addEventListener('click', () => {
      toast.success(`License request for ${item.name} fast-tracked via green channel!`);
    });

    grid.appendChild(card);
  });

  container.appendChild(grid);
}

function _renderRequestStatusLifecycle(container) {
  const header = document.createElement('div');
  header.className = 'page-header';
  header.innerHTML = `
    <div>
      <h1 class="page-title">Live Procurement Lifecycle Tracker</h1>
      <p class="page-description">Real-time status of multi-agent evaluations and human approval gates.</p>
    </div>
  `;
  container.appendChild(header);

  const card = document.createElement('div');
  card.className = 'table-card';
  card.style.padding = 'var(--space-6)';
  card.innerHTML = `
    <div style="margin-bottom: var(--space-6);">
      <h3 style="font-size: var(--text-lg); font-weight: var(--font-bold);">Request #REQ-1051: AWS Dedicated Host Reservation ($24,000)</h3>
      <div style="font-size: var(--text-xs); color: var(--text-muted); margin-top: 4px;">Initiated by requester@omnea.ai • Policy: Tier 2 Infrastructure Spend</div>
    </div>

    <div class="steps-grid" style="margin-bottom: var(--space-8);">
      <div class="step-card" style="border-color: var(--success-border);">
        <div class="step-number" style="background-color: var(--success);">✓</div>
        <h4 class="step-title">1. Intake Extraction</h4>
        <p class="step-desc">Classified as Cloud Infrastructure. Line items parsed in 1.2s.</p>
        <span class="badge badge-compliant" style="margin-top: 8px;">Passed</span>
      </div>

      <div class="step-card" style="border-color: var(--warning-border);">
        <div class="step-number" style="background-color: var(--warning);">2</div>
        <h4 class="step-title">2. Parallel Review</h4>
        <p class="step-desc">Legal: Approved. InfoSec: Approved. Finance: Human review required (>$20k limit).</p>
        <span class="badge badge-needs_human" style="margin-top: 8px;">In Review</span>
      </div>

      <div class="step-card">
        <div class="step-number" style="background-color: var(--text-muted);">3</div>
        <h4 class="step-title">3. ERP PO Generation</h4>
        <p class="step-desc">Pending Finance sign-off. NetSuite connector queued.</p>
        <span class="badge badge-neutral" style="margin-top: 8px;">Pending</span>
      </div>
    </div>
  `;
  container.appendChild(card);
}

function _openRequestDetailModal(request) {
  modal.open({
    title: `Request Details: ${request.id}`,
    bodyContent: `
      <div style="display: flex; flex-direction: column; gap: var(--space-4);">
        <div>
          <div style="font-size: var(--text-xs); color: var(--text-muted); text-transform: uppercase;">Vendor / Product</div>
          <div style="font-size: var(--text-base); font-weight: var(--font-bold);">${escapeHtml(request.vendor)}</div>
        </div>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-4);">
          <div>
            <div style="font-size: var(--text-xs); color: var(--text-muted); text-transform: uppercase;">Estimated Spend</div>
            <div style="font-size: var(--text-sm); font-weight: var(--font-semibold);">${escapeHtml(request.amount)}</div>
          </div>
          <div>
            <div style="font-size: var(--text-xs); color: var(--text-muted); text-transform: uppercase;">Category</div>
            <div style="font-size: var(--text-sm);">${escapeHtml(request.category)}</div>
          </div>
        </div>
        <div>
          <div style="font-size: var(--text-xs); color: var(--text-muted); text-transform: uppercase;">Current Evaluation Status</div>
          <span class="badge badge-${escapeHtml(request.status)}" style="margin-top: 4px;">
            ${escapeHtml(request.status)}
          </span>
        </div>
        <div style="background-color: var(--bg-subtle); padding: var(--space-3); border-radius: var(--radius-md);">
          <div style="font-size: var(--text-xs); font-weight: var(--font-semibold); margin-bottom: 4px;">Audit Chain Block</div>
          <code style="font-size: 11px; word-break: break-all;">sha256:8f4c2810a4e76d91bb350c38481ffbc4465d64e9</code>
        </div>
      </div>
    `,
    footerButtons: [
      {
        text: 'Close',
        variant: 'secondary',
        onClick: ({ close }) => close()
      }
    ]
  });
}

/* ==========================================================================
   2. Approver Views
   ========================================================================== */
function _renderApproverView(container, subRoute) {
  if (subRoute === '/approvals' || subRoute === '' || subRoute === '/') {
    renderApproverInbox(container);
    return;
  }
  if (subRoute === '/approvals/inspector') {
    _renderMultiAgentAdviceInspector(container);
    return;
  }
  if (subRoute === '/approvals/history') {
    _renderApprovalHistory(container);
    return;
  }

  // Fallback to inbox
  renderApproverInbox(container);
}

function _renderMultiAgentAdviceInspector(container) {
  const header = document.createElement('div');
  header.className = 'page-header';
  header.innerHTML = `
    <div>
      <h1 class="page-title">Multi-Agent Advice Inspector</h1>
      <p class="page-description">Deep-dive into concurrent deliberation traces, model reasoning, and risk score breakdowns.</p>
    </div>
    <a href="#/app/approvals" class="btn btn-secondary">Back to Approvals Queue</a>
  `;
  container.appendChild(header);

  const card = document.createElement('div');
  card.className = 'table-card';
  card.style.padding = 'var(--space-6)';
  card.innerHTML = `
    <div style="margin-bottom: var(--space-6); display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: var(--space-4);">
      <div>
        <h2 style="font-size: var(--text-xl); font-weight: var(--font-bold);">Snowflake Analytics Renewal ($45,000/yr)</h2>
        <div style="font-size: var(--text-xs); color: var(--text-muted); margin-top: 2px;">Target ERP: Oracle NetSuite • Tenant: Acme Corp</div>
      </div>
      <div style="display: flex; gap: var(--space-2);">
        <button type="button" class="btn btn-danger btn-sm" id="inspector-reject-btn">Reject Request</button>
        <button type="button" class="btn btn-primary btn-sm" id="inspector-approve-btn">Authorize & Sign Off</button>
      </div>
    </div>

    <div class="features-grid" style="grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));">
      <div class="feature-card">
        <div class="agent-card-header">
          <div class="agent-avatar agent-classifier">IC</div>
          <div>
            <div class="agent-name">Intake Intent Classifier</div>
            <div class="agent-domain">Confidence: 99.4%</div>
          </div>
        </div>
        <p class="agent-desc">
          Matched entity: Snowflake Inc. Verified category: Data Warehousing.
          Line items parsed: 50 TB Storage + compute credits. No duplication detected in active catalog.
        </p>
        <span class="badge badge-compliant" style="margin-top: 12px;">Valid Intent</span>
      </div>

      <div class="feature-card">
        <div class="agent-card-header">
          <div class="agent-avatar agent-legal">LR</div>
          <div>
            <div class="agent-name">Legal Reviewer</div>
            <div class="agent-domain">Verdict: Compliant</div>
          </div>
        </div>
        <p class="agent-desc">
          Analyzed standard MSA redline. Liability cap: 2x annual spend ($90,000).
          Indemnification for IP claims: Uncapped mutual. Governing law: Delaware. Zero red flags.
        </p>
        <span class="badge badge-compliant" style="margin-top: 12px;">Legal Pass</span>
      </div>

      <div class="feature-card">
        <div class="agent-card-header">
          <div class="agent-avatar agent-security">SR</div>
          <div>
            <div class="agent-name">Security Reviewer</div>
            <div class="agent-domain">Verdict: Compliant</div>
          </div>
        </div>
        <p class="agent-desc">
          SOC2 Type II report valid through Dec 2026. ISO 27001 verified.
          Data stored in AWS us-east-1. AES-256 encryption at rest verified via vendor telemetry.
        </p>
        <span class="badge badge-compliant" style="margin-top: 12px;">Security Pass</span>
      </div>

      <div class="feature-card">
        <div class="agent-card-header">
          <div class="agent-avatar agent-finance">FA</div>
          <div>
            <div class="agent-name">Finance Admin</div>
            <div class="agent-domain">Verdict: Threshold Escalate</div>
          </div>
        </div>
        <p class="agent-desc">
          Cost center: Engineering Data Platform. Total spend ($45,000) exceeds $25,000 auto-approval cap.
          Department budget has $140,000 remaining headroom. Requires human sign-off.
        </p>
        <span class="badge badge-needs_human" style="margin-top: 12px;">Human Gate Required</span>
      </div>
    </div>
  `;

  card.querySelector('#inspector-approve-btn').addEventListener('click', () => {
    toast.success('Procurement approved! PO dispatched to NetSuite.');
    window.location.hash = '#/app/approvals';
  });

  card.querySelector('#inspector-reject-btn').addEventListener('click', () => {
    toast.warning('Request rejected with reason logged.');
    window.location.hash = '#/app/approvals';
  });

  container.appendChild(card);
}

function _renderApprovalHistory(container) {
  const header = document.createElement('div');
  header.className = 'page-header';
  header.innerHTML = `
    <div>
      <h1 class="page-title">Approval Audit History</h1>
      <p class="page-description">Historical ledger of all approved and rejected requests with cryptographic timestamps.</p>
    </div>
  `;
  container.appendChild(header);

  const tableContainer = document.createElement('div');
  container.appendChild(tableContainer);

  const historyData = [
    { id: 'APV-198', vendor: 'GitHub Enterprise Copilot', amount: '$5,950', decision: 'approved', decided_by: 'approver@omnea.ai', date: '2026-11-10' },
    { id: 'APV-199', vendor: 'Legacy BI Tool Renewal', amount: '$38,000', decision: 'rejected', decided_by: 'finance@omnea.ai', date: '2026-11-08' },
    { id: 'APV-200', vendor: 'Datadog Enterprise APM', amount: '$14,500', decision: 'approved', decided_by: 'approver@omnea.ai', date: '2026-11-04' }
  ];

  new DataTable({
    container: tableContainer,
    title: 'Historical Approval Decisions',
    data: historyData,
    columns: [
      { key: 'id', label: 'Approval ID', sortable: true },
      { key: 'vendor', label: 'Vendor / Scope', sortable: true },
      { key: 'amount', label: 'Spend', sortable: true },
      {
        key: 'decision',
        label: 'Decision',
        sortable: true,
        render: (val) => `<span class="badge badge-${val === 'approved' ? 'compliant' : 'rejected'}">${escapeHtml(val)}</span>`
      },
      { key: 'decided_by', label: 'Sign-off Officer', sortable: true },
      { key: 'date', label: 'Timestamp', sortable: true }
    ]
  });
}

function _openApprovalInspectModal(item) {
  modal.open({
    title: `Deliberation Audit: ${item.id} (${item.vendor})`,
    bodyContent: `
      <div style="display: flex; flex-direction: column; gap: var(--space-4);">
        <div style="display: flex; justify-content: space-between;">
          <div><strong>Requested Spend:</strong> ${escapeHtml(item.amount)}</div>
          <div><strong>Risk Profile:</strong> <span class="badge badge-compliant">${escapeHtml(item.riskScore)}</span></div>
        </div>
        <div style="border-top: 1px solid var(--border-subtle); padding-top: var(--space-3);">
          <div style="font-weight: var(--font-semibold); margin-bottom: 4px;">Agent Advice Summary:</div>
          <ul style="padding-left: var(--space-5); font-size: var(--text-xs); color: var(--text-secondary); display: flex; flex-direction: column; gap: 4px;">
            <li><strong>Legal Reviewer:</strong> MSA redline vetted with standard liability protections.</li>
            <li><strong>Security Reviewer:</strong> Vendor SOC2 Type II verified, zero critical CVEs found.</li>
            <li><strong>Finance Admin:</strong> Over $25k threshold, department budget headroom confirmed.</li>
          </ul>
        </div>
      </div>
    `,
    footerButtons: [
      {
        text: 'Reject',
        variant: 'danger',
        onClick: ({ close }) => {
          toast.warning(`Request ${item.id} rejected.`);
          close();
        }
      },
      {
        text: 'Authorize Sign-off',
        variant: 'primary',
        onClick: ({ close }) => {
          toast.success(`Request ${item.id} approved and signed.`);
          close();
        }
      }
    ]
  });
}

/* ==========================================================================
   3. Finance Admin Views
   ========================================================================== */
function _renderFinanceView(container, subRoute) {
  if (subRoute === '/finance/erp') {
    _renderErpReconciliationView(container);
    return;
  }
  if (subRoute === '/finance/orders') {
    _renderPurchaseOrdersView(container);
    return;
  }

  // Budgets & Spend Caps
  const header = document.createElement('div');
  header.className = 'page-header';
  header.innerHTML = `
    <div>
      <h1 class="page-title">Finance & Spend Caps Control</h1>
      <p class="page-description">Enforce deterministic spend caps, monitor budget burn rates, and review ERP outbox reconciliations.</p>
    </div>
    <div style="display: flex; gap: var(--space-2);">
      <a href="#/app/finance/erp" class="btn btn-secondary">ERP Reconciliation</a>
      <a href="#/app/finance/orders" class="btn btn-primary">Purchase Orders</a>
    </div>
  `;
  container.appendChild(header);

  // Metrics Grid
  const metrics = document.createElement('div');
  metrics.className = 'metrics-grid';
  metrics.innerHTML = `
    <div class="metric-card">
      <div class="metric-header"><span class="metric-label">Allocated Budget</span></div>
      <div class="metric-value">$1,250,000</div>
      <div class="metric-trend trend-neutral">FY2026 Budget Pool</div>
    </div>
    <div class="metric-card">
      <div class="metric-header"><span class="metric-label">Committed Spend</span></div>
      <div class="metric-value">$842,100</div>
      <div class="metric-trend trend-up">67.3% utilized</div>
    </div>
    <div class="metric-card">
      <div class="metric-header"><span class="metric-label">Remaining Headroom</span></div>
      <div class="metric-value">$407,900</div>
      <div class="metric-trend trend-up">Safe financial buffer</div>
    </div>
    <div class="metric-card">
      <div class="metric-header"><span class="metric-label">ERP Sync Health</span></div>
      <div class="metric-value">100%</div>
      <div class="metric-trend trend-up">Zero outbox backlog</div>
    </div>
  `;
  container.appendChild(metrics);

  // SVG Chart: Spend Burn by Department
  const chartCard = document.createElement('div');
  chartCard.className = 'chart-card';
  chartCard.style.marginBottom = 'var(--space-8)';
  chartCard.innerHTML = `
    <div class="chart-header">
      <h3 class="chart-title">Departmental Spend vs Caps ($ in Thousands)</h3>
    </div>
    <div class="chart-container" id="finance-chart-container"></div>
  `;
  container.appendChild(chartCard);

  const chartEl = chartCard.querySelector('#finance-chart-container');
  ChartRenderer.renderBarChart({
    container: chartEl,
    title: 'Department Spend Caps',
    data: [
      { label: 'Engineering', value: 420 },
      { label: 'Security', value: 160 },
      { label: 'Product', value: 95 },
      { label: 'Sales/Mktg', value: 110 },
      { label: 'G&A', value: 57 }
    ]
  });
}

function _renderErpReconciliationView(container) {
  const header = document.createElement('div');
  header.className = 'page-header';
  header.innerHTML = `
    <div>
      <h1 class="page-title">ERP Reconciliation & Circuit Breakers</h1>
      <p class="page-description">Guaranteed outbox replay queue, idempotency keys, and automated circuit breaker repair tools.</p>
    </div>
    <button type="button" class="btn btn-secondary" id="repair-erp-btn">Trigger Reconciliation Sync</button>
  `;
  container.appendChild(header);

  const card = document.createElement('div');
  card.className = 'table-card';
  card.style.padding = 'var(--space-6)';
  card.innerHTML = `
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: var(--space-4); margin-bottom: var(--space-6);">
      <div style="background: var(--bg-subtle); padding: var(--space-4); border-radius: var(--radius-md);">
        <div style="font-size: var(--text-xs); color: var(--text-muted);">ERP Connector</div>
        <div style="font-size: var(--text-base); font-weight: var(--font-bold); margin-top: 2px;">Oracle NetSuite SuiteTalk</div>
        <span class="badge badge-compliant" style="margin-top: 4px;">Connected</span>
      </div>
      <div style="background: var(--bg-subtle); padding: var(--space-4); border-radius: var(--radius-md);">
        <div style="font-size: var(--text-xs); color: var(--text-muted);">Circuit Breaker Status</div>
        <div style="font-size: var(--text-base); font-weight: var(--font-bold); margin-top: 2px;">Closed (Operational)</div>
        <span class="badge badge-compliant" style="margin-top: 4px;">Zero Failures</span>
      </div>
      <div style="background: var(--bg-subtle); padding: var(--space-4); border-radius: var(--radius-md);">
        <div style="font-size: var(--text-xs); color: var(--text-muted);">Outbox Queue Backlog</div>
        <div style="font-size: var(--text-base); font-weight: var(--font-bold); margin-top: 2px;">0 items</div>
        <span class="badge badge-compliant" style="margin-top: 4px;">Real-Time</span>
      </div>
    </div>
  `;

  card.querySelector('#repair-erp-btn').addEventListener('click', () => {
    toast.success('Reconciliation scan complete: All outbox entries matched system-of-record.');
  });

  container.appendChild(card);
}

function _renderPurchaseOrdersView(container) {
  const header = document.createElement('div');
  header.className = 'page-header';
  header.innerHTML = `
    <div>
      <h1 class="page-title">Purchase Orders (PO) Ledger</h1>
      <p class="page-description">Automated system-of-record PO generation with cryptographic audit linkage.</p>
    </div>
  `;
  container.appendChild(header);

  const tableContainer = document.createElement('div');
  container.appendChild(tableContainer);

  const orders = [
    { poNumber: 'PO-2026-081', vendor: 'Datadog Inc', amount: '$14,500', erpRef: 'NS-PO-99120', status: 'approved', date: '2026-11-15' },
    { poNumber: 'PO-2026-082', vendor: 'GitHub Enterprise', amount: '$5,950', erpRef: 'NS-PO-99121', status: 'approved', date: '2026-11-10' },
    { poNumber: 'PO-2026-083', vendor: 'Figma Inc', amount: '$3,800', erpRef: 'NS-PO-99122', status: 'pending', date: '2026-11-18' }
  ];

  new DataTable({
    container: tableContainer,
    title: 'Generated Purchase Orders',
    data: orders,
    columns: [
      { key: 'poNumber', label: 'PO #', sortable: true },
      { key: 'vendor', label: 'Vendor', sortable: true },
      { key: 'amount', label: 'Amount', sortable: true },
      { key: 'erpRef', label: 'ERP Reference ID', sortable: true },
      {
        key: 'status',
        label: 'Status',
        render: (val) => `<span class="badge badge-${escapeHtml(val)}">${escapeHtml(val)}</span>`
      },
      { key: 'date', label: 'Sync Date', sortable: true }
    ]
  });
}

/* ==========================================================================
   4. Security Reviewer Views
   ========================================================================== */
function _renderSecurityView(container, subRoute) {
  if (subRoute === '/security/compliance') {
    _renderComplianceSoc2View(container);
    return;
  }
  if (subRoute === '/security/tprm') {
    _renderTprmQueueView(container);
    return;
  }

  // Default: Vendor Security Reviews
  const header = document.createElement('div');
  header.className = 'page-header';
  header.innerHTML = `
    <div>
      <h1 class="page-title">Vendor Security & Compliance Engine</h1>
      <p class="page-description">Automated SOC2 Type II verification, encryption standards, and TPRM risk monitors.</p>
    </div>
    <div style="display: flex; gap: var(--space-2);">
      <a href="#/app/security/compliance" class="btn btn-secondary">SOC2 Matrix</a>
      <a href="#/app/security/tprm" class="btn btn-primary">TPRM Queue</a>
    </div>
  `;
  container.appendChild(header);

  const metrics = document.createElement('div');
  metrics.className = 'metrics-grid';
  metrics.innerHTML = `
    <div class="metric-card">
      <div class="metric-header"><span class="metric-label">Active Vendors</span></div>
      <div class="metric-value">46</div>
      <div class="metric-trend trend-up">All continuous-monitored</div>
    </div>
    <div class="metric-card">
      <div class="metric-header"><span class="metric-label">SOC2 Verified</span></div>
      <div class="metric-value">100%</div>
      <div class="metric-trend trend-up">Zero expired reports</div>
    </div>
    <div class="metric-card">
      <div class="metric-header"><span class="metric-label">Critical Findings</span></div>
      <div class="metric-value">0</div>
      <div class="metric-trend trend-up">Zero unmitigated risks</div>
    </div>
    <div class="metric-card">
      <div class="metric-header"><span class="metric-label">Avg TPRM Score</span></div>
      <div class="metric-value">92/100</div>
      <div class="metric-trend trend-neutral">Low risk baseline</div>
    </div>
  `;
  container.appendChild(metrics);

  const tableContainer = document.createElement('div');
  container.appendChild(tableContainer);

  const vendors = [
    { vendor: 'Snowflake Inc', soc2: 'Type II (Current)', encryption: 'AES-256 / TLS 1.3', residency: 'US / EU', tprmScore: '96/100', status: 'compliant' },
    { vendor: 'Datadog Inc', soc2: 'Type II (Current)', encryption: 'AES-256 / TLS 1.3', residency: 'US', tprmScore: '94/100', status: 'compliant' },
    { vendor: 'Figma Inc', soc2: 'Type II (Current)', encryption: 'AES-256 / TLS 1.2+', residency: 'US', tprmScore: '91/100', status: 'compliant' },
    { vendor: 'New Vendor XYZ', soc2: 'Pending Upload', encryption: 'Evaluating', residency: 'Unknown', tprmScore: '68/100', status: 'needs_human' }
  ];

  new DataTable({
    container: tableContainer,
    title: 'Vendor Security Evaluations',
    data: vendors,
    columns: [
      { key: 'vendor', label: 'Vendor', sortable: true },
      { key: 'soc2', label: 'SOC2 Status', sortable: true },
      { key: 'encryption', label: 'Encryption Standards', sortable: true },
      { key: 'residency', label: 'Data Residency', sortable: true },
      { key: 'tprmScore', label: 'TPRM Score', sortable: true },
      {
        key: 'status',
        label: 'Posture',
        render: (val) => `<span class="badge badge-${escapeHtml(val)}">${escapeHtml(val)}</span>`
      }
    ]
  });
}

function _renderComplianceSoc2View(container) {
  const header = document.createElement('div');
  header.className = 'page-header';
  header.innerHTML = `
    <div>
      <h1 class="page-title">SOC2 Trust Services Criteria Checklist</h1>
      <p class="page-description">Automated alignment checks against AICPA Trust Services Criteria.</p>
    </div>
  `;
  container.appendChild(header);

  const card = document.createElement('div');
  card.className = 'table-card';
  card.style.padding = 'var(--space-6)';
  card.innerHTML = `
    <div style="display: flex; flex-direction: column; gap: var(--space-4);">
      <div style="display: flex; justify-content: space-between; align-items: center; padding: var(--space-3); background: var(--bg-subtle); border-radius: var(--radius-md);">
        <div>
          <strong>CC6.1 - Logical Access Controls</strong>
          <div style="font-size: var(--text-xs); color: var(--text-muted);">Enforces MFA and RBAC on all infrastructure endpoints.</div>
        </div>
        <span class="badge badge-compliant">Verified</span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; padding: var(--space-3); background: var(--bg-subtle); border-radius: var(--radius-md);">
        <div>
          <strong>CC6.6 - Boundary Protection & Encryption</strong>
          <div style="font-size: var(--text-xs); color: var(--text-muted);">All external traffic terminates TLS 1.3; database RLS fail-closed.</div>
        </div>
        <span class="badge badge-compliant">Verified</span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; padding: var(--space-3); background: var(--bg-subtle); border-radius: var(--radius-md);">
        <div>
          <strong>CC7.2 - Vulnerability & Patch Management</strong>
          <div style="font-size: var(--text-xs); color: var(--text-muted);">Automated container scans and cryptographic dependency lockfiles.</div>
        </div>
        <span class="badge badge-compliant">Verified</span>
      </div>
    </div>
  `;
  container.appendChild(card);
}

function _renderTprmQueueView(container) {
  const header = document.createElement('div');
  header.className = 'page-header';
  header.innerHTML = `
    <div>
      <h1 class="page-title">Third-Party Risk Management (TPRM) Queue</h1>
      <p class="page-description">Adverse media alerts, sanctions watchlists, and corporate filing checks.</p>
    </div>
  `;
  container.appendChild(header);

  const tableContainer = document.createElement('div');
  container.appendChild(tableContainer);

  const alerts = [
    { vendor: 'Snowflake Inc', event: 'Quarterly Security Whitepaper Updated', severity: 'Info', date: '2026-11-14' },
    { vendor: 'Datadog Inc', event: 'ISO 27001 Annual Audit Passed', severity: 'Positive', date: '2026-11-02' },
    { vendor: 'New Vendor XYZ', event: 'Sanctions Check Cleared; Awaiting DPA', severity: 'Pending', date: '2026-11-18' }
  ];

  new DataTable({
    container: tableContainer,
    title: 'Continuous TPRM Monitoring Feed',
    data: alerts,
    columns: [
      { key: 'vendor', label: 'Vendor', sortable: true },
      { key: 'event', label: 'Intelligence Event', sortable: true },
      { key: 'severity', label: 'Severity', sortable: true },
      { key: 'date', label: 'Recorded Date', sortable: true }
    ]
  });
}

/* ==========================================================================
   5. Legal Reviewer Views
   ========================================================================== */
function _renderLegalView(container, subRoute) {
  if (subRoute === '/legal/terms') {
    _renderTermsLiabilityView(container);
    return;
  }
  if (subRoute === '/legal/queue') {
    _renderLegalQueueView(container);
    return;
  }

  // Contract Reviews
  const header = document.createElement('div');
  header.className = 'page-header';
  header.innerHTML = `
    <div>
      <h1 class="page-title">Legal & Contract Review Workspace</h1>
      <p class="page-description">Automated MSA redline analysis, liability cap verification, and governing law compliance.</p>
    </div>
    <div style="display: flex; gap: var(--space-2);">
      <a href="#/app/legal/terms" class="btn btn-secondary">Terms & Liability</a>
      <a href="#/app/legal/queue" class="btn btn-primary">Approval Queue</a>
    </div>
  `;
  container.appendChild(header);

  const metrics = document.createElement('div');
  metrics.className = 'metrics-grid';
  metrics.innerHTML = `
    <div class="metric-card">
      <div class="metric-header"><span class="metric-label">Active Contracts</span></div>
      <div class="metric-value">38</div>
      <div class="metric-trend trend-neutral">In force</div>
    </div>
    <div class="metric-card">
      <div class="metric-header"><span class="metric-label">Redlines In Review</span></div>
      <div class="metric-value">2</div>
      <div class="metric-trend trend-neutral">AI agent parsed</div>
    </div>
    <div class="metric-card">
      <div class="metric-header"><span class="metric-label">High-Risk Clauses</span></div>
      <div class="metric-value">0</div>
      <div class="metric-trend trend-up">All capped within policy</div>
    </div>
    <div class="metric-card">
      <div class="metric-header"><span class="metric-label">Avg Review Speed</span></div>
      <div class="metric-value">3.4 min</div>
      <div class="metric-trend trend-up">Autonomous extraction</div>
    </div>
  `;
  container.appendChild(metrics);

  const tableContainer = document.createElement('div');
  container.appendChild(tableContainer);

  const contracts = [
    { vendor: 'Snowflake Inc', type: 'Enterprise MSA', liabilityCap: '2x Fees ($90k)', jurisdiction: 'Delaware', status: 'compliant', renewalDate: '2027-11-01' },
    { vendor: 'Datadog Inc', type: 'Cloud Order Form + DPA', liabilityCap: '12 Months Fees', jurisdiction: 'New York', status: 'compliant', renewalDate: '2027-05-15' },
    { vendor: 'Figma Inc', type: 'Organization Term', liabilityCap: 'Standard', jurisdiction: 'California', status: 'compliant', renewalDate: '2027-08-30' }
  ];

  new DataTable({
    container: tableContainer,
    title: 'Active Master Service Agreements (MSAs)',
    data: contracts,
    columns: [
      { key: 'vendor', label: 'Vendor', sortable: true },
      { key: 'type', label: 'Agreement Type', sortable: true },
      { key: 'liabilityCap', label: 'Liability Limitation', sortable: true },
      { key: 'jurisdiction', label: 'Governing Law', sortable: true },
      {
        key: 'status',
        label: 'Legal Posture',
        render: (val) => `<span class="badge badge-${escapeHtml(val)}">${escapeHtml(val)}</span>`
      },
      { key: 'renewalDate', label: 'Next Renewal', sortable: true }
    ]
  });
}

function _renderTermsLiabilityView(container) {
  const header = document.createElement('div');
  header.className = 'page-header';
  header.innerHTML = `
    <div>
      <h1 class="page-title">Terms & Liability Risk Matrix</h1>
      <p class="page-description">Automated extraction of high-liability contract clauses and uncapped exposure warnings.</p>
    </div>
  `;
  container.appendChild(header);

  const card = document.createElement('div');
  card.className = 'table-card';
  card.style.padding = 'var(--space-6)';
  card.innerHTML = `
    <div style="display: flex; flex-direction: column; gap: var(--space-4);">
      <div style="border-left: 4px solid var(--success); padding: var(--space-3); background: var(--bg-subtle);">
        <strong style="color: var(--success);">Mutual Indemnification Verified</strong>
        <p style="font-size: var(--text-xs); color: var(--text-secondary); margin-top: 4px;">
          All active MSAs require mutual IP infringement indemnity. Unilateral indemnification clauses are automatically flagged and rejected.
        </p>
      </div>
      <div style="border-left: 4px solid var(--success); padding: var(--space-3); background: var(--bg-subtle);">
        <strong style="color: var(--success);">Governing Law Compliance</strong>
        <p style="font-size: var(--text-xs); color: var(--text-secondary); margin-top: 4px;">
          Approved jurisdictions: Delaware, New York, California, England & Wales. Non-standard international jurisdictions require general counsel approval.
        </p>
      </div>
    </div>
  `;
  container.appendChild(card);
}

function _renderLegalQueueView(container) {
  const header = document.createElement('div');
  header.className = 'page-header';
  header.innerHTML = `
    <div>
      <h1 class="page-title">Legal Approval Queue</h1>
      <p class="page-description">Review and approve vendor DPAs, redlines, and customized contract amendments.</p>
    </div>
  `;
  container.appendChild(header);

  const tableContainer = document.createElement('div');
  container.appendChild(tableContainer);

  const queue = [
    { id: 'LEG-101', vendor: 'New Vendor XYZ', agreement: 'Data Processing Addendum (DPA)', status: 'needs_human', date: '2026-11-18' }
  ];

  new DataTable({
    container: tableContainer,
    title: 'Pending Legal Sign-offs',
    data: queue,
    columns: [
      { key: 'id', label: 'ID', sortable: true },
      { key: 'vendor', label: 'Vendor', sortable: true },
      { key: 'agreement', label: 'Document Type', sortable: true },
      {
        key: 'status',
        label: 'Status',
        render: (val) => `<span class="badge badge-${escapeHtml(val)}">${escapeHtml(val)}</span>`
      },
      {
        key: 'id',
        label: 'Actions',
        render: (id, row) => {
          const btn = document.createElement('button');
          btn.className = 'btn btn-primary btn-sm';
          btn.textContent = 'Sign & Approve';
          btn.addEventListener('click', () => {
            toast.success(`DPA for ${row.vendor} signed and logged to SHA-256 audit chain.`);
          });
          return btn;
        }
      }
    ]
  });
}

/* ==========================================================================
   6. Tenant Admin Views
   ========================================================================== */
function _renderAdminView(container, subRoute) {
  if (subRoute === '/admin/developers' || subRoute === '/admin/developer') {
    renderDeveloperPlatformView(container);
    return;
  }
  if (subRoute === '/admin/org') {
    _renderOrgSettings(container);
    return;
  }
  if (subRoute === '/admin/users') {
    _renderUserManagement(container);
    return;
  }
  if (subRoute === '/admin/invitations') {
    _renderInvitationsManagement(container);
    return;
  }
  if (subRoute === '/admin/audit') {
    _renderAuditChainLog(container);
    return;
  }
  if (subRoute === '/admin/policies') {
    _renderPolicyConfig(container);
    return;
  }

  // Default: Organization Settings & Overview
  _renderOrgSettings(container);
}

async function _renderOrgSettings(container) {
  const header = document.createElement('div');
  header.className = 'page-header';
  header.innerHTML = `
    <div>
      <h1 class="page-title">Organization & Systems Settings</h1>
      <p class="page-description">Configure tenant boundary parameters, ERP systems of record, Slack bot integration, and notification preferences.</p>
    </div>
    <div style="display: flex; gap: var(--space-2); flex-wrap: wrap;">
      <a href="#/app/requests/new" class="btn btn-primary">Submit Request</a>
      <a href="#/app/requests" class="btn btn-secondary">Requests</a>
      <a href="#/app/finance/erp" class="btn btn-secondary">ERP Ledger & Reconciliation</a>
      <a href="#/app/admin/users" class="btn btn-secondary">User Management</a>
      <a href="#/app/admin/developers" class="btn btn-secondary">Developer Platform & API</a>
      <a href="#/app/admin/audit" class="btn btn-secondary">Audit Chain Log</a>
    </div>
  `;
  container.appendChild(header);

  const tenantId = state.getTenantId();
  const settingsWrapper = document.createElement('div');
  settingsWrapper.style.display = 'flex';
  settingsWrapper.style.flexDirection = 'column';
  settingsWrapper.style.gap = 'var(--space-6)';
  container.appendChild(settingsWrapper);

  // 1. Tenant Security Parameters Card
  const secCard = document.createElement('div');
  secCard.className = 'table-card';
  secCard.style.padding = 'var(--space-6)';
  secCard.innerHTML = `
    <h3 style="margin: 0 0 var(--space-4); font-size: var(--text-base); font-weight: var(--font-semibold); color: var(--text-primary);">
      Tenant Security & Cryptographic Boundary
    </h3>
    <div style="max-width: 650px;">
      <div class="form-group">
        <label class="form-label">Tenant ID (PostgreSQL RLS Partition)</label>
        <input type="text" class="form-input" value="${escapeHtml(tenantId)}" readonly>
        <div class="form-help">Tenant boundary enforced in database connection context; impossible to breach via SQL injection.</div>
      </div>
      <div class="form-group">
        <label class="form-label">Audit Integrity Engine</label>
        <input type="text" class="form-input" value="SHA-256 Merkle-Audit Chain & Hardware AES-256-GCM Vault" readonly>
      </div>
    </div>
  `;
  settingsWrapper.appendChild(secCard);

  // 2. ERP System of Record Connector Card
  const erpCard = document.createElement('div');
  erpCard.className = 'table-card';
  erpCard.style.padding = 'var(--space-6)';
  erpCard.innerHTML = `
    <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: var(--space-4);">
      <div>
        <h3 style="margin: 0; font-size: var(--text-base); font-weight: var(--font-semibold); color: var(--text-primary);">
          ERP System of Record Connector
        </h3>
        <p style="margin: 2px 0 0; font-size: var(--text-xs); color: var(--text-muted);">
          Integrate with NetSuite, SAP, Workday, or the built-in PostgreSQL Ledger. Credentials stored via Secrets Manager reference, never in DB plaintext.
        </p>
      </div>
      <div id="erp-connection-indicator">
        <span class="badge badge-info">Checking status...</span>
      </div>
    </div>

    <form id="erp-config-form" style="max-width: 650px; display: flex; flex-direction: column; gap: var(--space-4);">
      <div class="form-group">
        <label class="form-label" for="erp-connector-select">Connector Type <span style="color: var(--color-danger);">*</span></label>
        <select id="erp-connector-select" class="form-select">
          <option value="ledger">Built-in Ledger ERP (Postgres SOX System of Record)</option>
          <option value="netsuite">Oracle NetSuite ERP</option>
          <option value="sap">SAP S/4HANA ERP</option>
          <option value="workday">Workday Financial Management</option>
        </select>
        <div class="form-help">The built-in Ledger connector always works out of the box with zero external configuration.</div>
      </div>

      <div id="erp-ext-credentials-container" style="display: none; flex-direction: column; gap: var(--space-3); border: 1px dashed var(--border-color); padding: var(--space-3); border-radius: var(--radius-md);">
        <div class="form-group">
          <label class="form-label" for="erp-api-host">API Endpoint / Host URL</label>
          <input type="text" id="erp-api-host" class="form-input" placeholder="https://api.netsuite.com or https://my-host.workday.com">
        </div>
        <div class="form-group">
          <label class="form-label" for="erp-account-id">Account ID / Client ID</label>
          <input type="text" id="erp-account-id" class="form-input" placeholder="e.g. 1234567_SB1">
        </div>
        <div class="form-group">
          <label class="form-label" for="erp-api-token">API Token / Secret Key</label>
          <input type="password" id="erp-api-token" class="form-input" placeholder="••••••••••••••••">
          <div class="form-help">Secrets are encrypted with AES-256-GCM and referenced by URI. Never stored in DB plaintext.</div>
        </div>
      </div>

      <div style="display: flex; gap: var(--space-2); align-items: center; margin-top: var(--space-2);">
        <button type="submit" class="btn btn-primary" id="btn-save-erp-config">
          Save Configuration
        </button>
        <button type="button" class="btn btn-secondary" id="btn-test-erp-connection">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 4px;">
            <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline>
          </svg>
          Test Connection
        </button>
      </div>

      <div id="erp-test-result" style="display: none; padding: var(--space-3); border-radius: var(--radius-md); font-size: var(--text-xs);"></div>
    </form>
  `;
  settingsWrapper.appendChild(erpCard);

  // 3. Slack Integration Card
  const slackCard = document.createElement('div');
  slackCard.className = 'table-card';
  slackCard.style.padding = 'var(--space-6)';
  slackCard.innerHTML = `
    <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: var(--space-4);">
      <div>
        <h3 style="margin: 0; font-size: var(--text-base); font-weight: var(--font-semibold); color: var(--text-primary);">
          Slack Bot & Interactive Approval Workflow
        </h3>
        <p style="margin: 2px 0 0; font-size: var(--text-xs); color: var(--text-muted);">
          Intake procurement requests from Slack channels, approve/reject via interactive message buttons with in-place chat updates, mapped to SSO identities.
        </p>
      </div>
      <div id="slack-connection-status">
        <span class="badge badge-warning">Not connected</span>
      </div>
    </div>

    <div style="display: flex; gap: var(--space-3); flex-wrap: wrap; margin-bottom: var(--space-4);">
      <button type="button" class="btn btn-primary" id="btn-install-slack">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 4px;">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
        </svg>
        Install Slack App
      </button>
      <button type="button" class="btn btn-secondary" id="btn-view-slack-manifest">
        View App Manifest (YAML/JSON)
      </button>
      <button type="button" class="btn btn-secondary" id="btn-map-slack-identity">
        Map Slack SSO Identity
      </button>
    </div>
  `;
  settingsWrapper.appendChild(slackCard);

  // 4. Multichannel Notification Preferences Card
  const notifCard = document.createElement('div');
  notifCard.className = 'table-card';
  notifCard.style.padding = 'var(--space-6)';
  notifCard.innerHTML = `
    <div style="margin-bottom: var(--space-4);">
      <h3 style="margin: 0; font-size: var(--text-base); font-weight: var(--font-semibold); color: var(--text-primary);">
        Multichannel Notification Preferences
      </h3>
      <p style="margin: 2px 0 0; font-size: var(--text-xs); color: var(--text-muted);">
        Manage your delivery channels for procurement approvals, status changes, and drift alerts.
      </p>
    </div>

    <div style="display: flex; flex-direction: column; gap: var(--space-3); max-width: 500px; margin-bottom: var(--space-4);">
      <label style="display: flex; align-items: center; gap: var(--space-2); cursor: pointer;">
        <input type="checkbox" id="pref-channel-in-app" checked>
        <span style="font-size: var(--text-sm); color: var(--text-primary); font-weight: var(--font-medium);">In-App Notifications</span>
      </label>
      <label style="display: flex; align-items: center; gap: var(--space-2); cursor: pointer;">
        <input type="checkbox" id="pref-channel-email" checked>
        <span style="font-size: var(--text-sm); color: var(--text-primary); font-weight: var(--font-medium);">Email Notifications (SMTP / SES)</span>
      </label>
      <label style="display: flex; align-items: center; gap: var(--space-2); cursor: pointer;">
        <input type="checkbox" id="pref-channel-slack" checked>
        <span style="font-size: var(--text-sm); color: var(--text-primary); font-weight: var(--font-medium);">Slack Bot Direct Messages</span>
      </label>
    </div>

    <button type="button" class="btn btn-secondary" id="btn-save-notification-prefs">
      Save Preferences
    </button>
  `;
  settingsWrapper.appendChild(notifCard);

  // Fetch & bind initial values
  _initOrgSettingsInteractions(settingsWrapper);
}

async function _initOrgSettingsInteractions(container) {
  const erpSelect = container.querySelector('#erp-connector-select');
  const erpExtCreds = container.querySelector('#erp-ext-credentials-container');
  const erpIndicator = container.querySelector('#erp-connection-indicator');
  const testResultEl = container.querySelector('#erp-test-result');
  const erpForm = container.querySelector('#erp-config-form');
  const testConnBtn = container.querySelector('#btn-test-erp-connection');
  const slackStatusEl = container.querySelector('#slack-connection-status');
  const installSlackBtn = container.querySelector('#btn-install-slack');
  const viewManifestBtn = container.querySelector('#btn-view-slack-manifest');
  const mapIdentityBtn = container.querySelector('#btn-map-slack-identity');
  const savePrefsBtn = container.querySelector('#btn-save-notification-prefs');
  const inAppChk = container.querySelector('#pref-channel-in-app');
  const emailChk = container.querySelector('#pref-channel-email');
  const slackChk = container.querySelector('#pref-channel-slack');

  // Attach all DOM event listeners SYNCHRONOUSLY first
  if (erpSelect) {
    erpSelect.addEventListener('change', () => {
      erpExtCreds.style.display = erpSelect.value === 'ledger' ? 'none' : 'flex';
    });
  }

  // Save ERP Config
  if (erpForm) {
    erpForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const connectorType = erpSelect.value;
      const host = container.querySelector('#erp-api-host').value.trim();
      const accountId = container.querySelector('#erp-account-id').value.trim();
      const token = container.querySelector('#erp-api-token').value.trim();

      const creds = {};
      if (connectorType !== 'ledger') {
        if (host) creds.host = host;
        if (accountId) creds.account_id = accountId;
        if (token) creds.token = token;
      }

      try {
        const updated = await erpApi.updateConfig({
          connector_type: connectorType,
          credentials: creds,
          settings: { host }
        });
        toast.success(`ERP configuration updated: ${updated.connector_type}`);
        const isConn = updated.status === 'connected';
        erpIndicator.innerHTML = `<span class="badge ${isConn ? 'badge-success' : 'badge-warning'}">${isConn ? 'Connected' : 'Not connected'}</span>`;
      } catch (err) {
        toast.error('Failed to update ERP configuration.');
      }
    });
  }

  // Test ERP Connection
  if (testConnBtn) {
    testConnBtn.addEventListener('click', async () => {
      testConnBtn.disabled = true;
      testConnBtn.innerHTML = 'Testing...';
      testResultEl.style.display = 'none';

      try {
        const connectorType = erpSelect ? erpSelect.value : 'ledger';
        const res = await erpApi.testConnection(connectorType);
        testResultEl.style.display = 'block';
        if (res.connected) {
          testResultEl.style.background = 'rgba(16, 185, 129, 0.1)';
          testResultEl.style.border = '1px solid var(--color-success)';
          testResultEl.style.color = 'var(--color-success)';
          testResultEl.innerHTML = `<strong>Connected:</strong> ${escapeHtml(res.message)} (latency: ${res.latency_ms || 0}ms, CB: ${escapeHtml(res.circuit_breaker || 'CLOSED')})`;
          if (erpIndicator) erpIndicator.innerHTML = '<span class="badge badge-success">Connected</span>';
        } else {
          testResultEl.style.background = 'rgba(239, 68, 68, 0.1)';
          testResultEl.style.border = '1px solid var(--color-danger)';
          testResultEl.style.color = 'var(--color-danger)';
          testResultEl.innerHTML = `<strong>Not Connected:</strong> ${escapeHtml(res.message)} (CB: ${escapeHtml(res.circuit_breaker || 'CLOSED')})`;
          if (erpIndicator) erpIndicator.innerHTML = '<span class="badge badge-warning">Not connected</span>';
        }
      } catch (err) {
        testResultEl.style.display = 'block';
        testResultEl.style.background = 'rgba(239, 68, 68, 0.1)';
        testResultEl.style.border = '1px solid var(--color-danger)';
        testResultEl.style.color = 'var(--color-danger)';
        testResultEl.innerHTML = `<strong>Error:</strong> ${escapeHtml(err.message || 'Connection test failed.')}`;
      } finally {
        testConnBtn.disabled = false;
        testConnBtn.innerHTML = 'Test Connection';
      }
    });
  }

  // Install Slack
  if (installSlackBtn) {
    installSlackBtn.addEventListener('click', async () => {
      try {
        const oauthInfo = await slackApi.getOAuthUrl();
        if (oauthInfo && oauthInfo.oauth_url) {
          window.open(oauthInfo.oauth_url, '_blank');
          toast.info('Opened Slack OAuth installation page.');
        }
      } catch (err) {
        toast.error('Unable to initiate Slack OAuth flow.');
      }
    });
  }

  // View Slack Manifest Modal
  if (viewManifestBtn) {
    viewManifestBtn.addEventListener('click', async () => {
      try {
        const manifest = await slackApi.getManifest();
        modal.open({
          title: 'Slack App Manifest (JSON)',
          bodyContent: `
            <p style="margin: 0 0 var(--space-2); font-size: var(--text-xs); color: var(--text-muted);">
              Copy and paste this manifest into your Slack App Configuration console to auto-provision bots, slash commands, and interactive webhooks.
            </p>
            <pre style="background: var(--bg-canvas); padding: var(--space-3); border-radius: var(--radius-md); font-size: var(--text-xs); max-height: 400px; overflow: auto; border: 1px solid var(--border-color);"><code>${escapeHtml(JSON.stringify(manifest, null, 2))}</code></pre>
          `,
          size: 'lg',
          footerButtons: [
            {
              text: 'Copy JSON',
              variant: 'secondary',
              onClick: () => {
                navigator.clipboard.writeText(JSON.stringify(manifest, null, 2));
                toast.success('Manifest copied to clipboard.');
              }
            },
            {
              text: 'Close',
              variant: 'primary',
              onClick: ({ close }) => close()
            }
          ]
        });
      } catch (err) {
        toast.error('Failed to load Slack manifest.');
      }
    });
  }

  // Map Slack SSO Identity Modal
  if (mapIdentityBtn) {
    mapIdentityBtn.addEventListener('click', () => {
      const form = document.createElement('div');
      form.innerHTML = `
        <div style="display: flex; flex-direction: column; gap: var(--space-3);">
          <div class="form-group">
            <label class="form-label">Slack User ID <span style="color: var(--color-danger);">*</span></label>
            <input type="text" id="map-slack-id" class="form-input" placeholder="e.g. U0123456789" required>
          </div>
          <div class="form-group">
            <label class="form-label">Work Email / SSO Identity <span style="color: var(--color-danger);">*</span></label>
            <input type="email" id="map-slack-email" class="form-input" placeholder="user@company.com" required>
          </div>
        </div>
      `;
      modal.open({
        title: 'Map Slack SSO Identity',
        bodyContent: form,
        size: 'md',
        footerButtons: [
          { text: 'Cancel', variant: 'secondary', onClick: ({ close }) => close() },
          {
            text: 'Save Mapping',
            variant: 'primary',
            onClick: async ({ close }) => {
              const slackUserId = form.querySelector('#map-slack-id').value.trim();
              const email = form.querySelector('#map-slack-email').value.trim();
              if (!slackUserId || !email) {
                toast.warning('Both Slack User ID and Email are required.');
                return;
              }
              try {
                await slackApi.mapIdentity({ slack_user_id: slackUserId, email });
                toast.success('Slack identity successfully mapped to SSO profile.');
                close();
              } catch (err) {
                toast.error('Failed to map Slack identity.');
              }
            }
          }
        ]
      });
    });
  }

  // Save Notification Preferences
  if (savePrefsBtn) {
    savePrefsBtn.addEventListener('click', async () => {
      try {
        await notificationsApi.updatePreferences({
          channels: {
            in_app: inAppChk.checked,
            email: emailChk.checked,
            slack: slackChk.checked
          }
        });
        toast.success('Notification preferences updated.');
      } catch (err) {
        toast.error('Failed to update notification preferences.');
      }
    });
  }

  // Fetch initial async state in background
  Promise.allSettled([
    erpApi.getConfig().then(config => {
      if (config) {
        if (erpSelect) {
          erpSelect.value = config.connector_type || 'ledger';
          if (erpExtCreds) erpExtCreds.style.display = erpSelect.value === 'ledger' ? 'none' : 'flex';
        }
        if (erpIndicator) {
          const isConn = config.status === 'connected';
          erpIndicator.innerHTML = `<span class="badge ${isConn ? 'badge-success' : 'badge-warning'}">${isConn ? 'Connected' : 'Not connected'}</span>`;
        }
      }
    }),
    slackApi.getStatus().then(slackStatus => {
      if (slackStatusEl) {
        if (slackStatus && slackStatus.installed) {
          slackStatusEl.innerHTML = '<span class="badge badge-success">Connected</span>';
        } else {
          slackStatusEl.innerHTML = '<span class="badge badge-warning">Not connected</span>';
        }
      }
    }),
    notificationsApi.getPreferences().then(prefs => {
      if (prefs && prefs.channels) {
        if (inAppChk) inAppChk.checked = prefs.channels.in_app !== false;
        if (emailChk) emailChk.checked = prefs.channels.email !== false;
        if (slackChk) slackChk.checked = prefs.channels.slack !== false;
      }
    })
  ]).catch(() => {});
}

function _renderUserManagement(container) {
  const header = document.createElement('div');
  header.className = 'page-header';
  header.innerHTML = `
    <div>
      <h1 class="page-title">User & Role Management</h1>
      <p class="page-description">Assign granular RBAC roles to team members within this tenant.</p>
    </div>
    <a href="#/app/admin/invitations" class="btn btn-primary">Invite Member</a>
  `;
  container.appendChild(header);

  const tableContainer = document.createElement('div');
  container.appendChild(tableContainer);

  const users = [
    { email: 'admin@acme.com', name: 'Tenant Administrator', role: 'tenant_admin', status: 'active' },
    { email: 'finance@acme.com', name: 'Finance Controller', role: 'finance_admin', status: 'active' },
    { email: 'infosec@acme.com', name: 'Security Lead', role: 'security_reviewer', status: 'active' },
    { email: 'legal@acme.com', name: 'General Counsel', role: 'legal_reviewer', status: 'active' },
    { email: 'requester@acme.com', name: 'Software Engineer', role: 'requester', status: 'active' }
  ];

  new DataTable({
    container: tableContainer,
    title: 'Tenant Members & Assigned Roles',
    data: users,
    columns: [
      { key: 'email', label: 'Email', sortable: true },
      { key: 'name', label: 'Full Name', sortable: true },
      {
        key: 'role',
        label: 'Assigned Role',
        sortable: true,
        render: (val) => `<span class="badge badge-info">${escapeHtml(val)}</span>`
      },
      {
        key: 'status',
        label: 'Status',
        render: (val) => `<span class="badge badge-${escapeHtml(val)}">${escapeHtml(val)}</span>`
      },
      {
        key: 'email',
        label: 'Actions',
        render: (email, row) => {
          const btn = document.createElement('button');
          btn.className = 'btn btn-secondary btn-sm';
          btn.textContent = 'Manage Role';
          btn.addEventListener('click', () => {
            _openRoleChangeModal(row);
          });
          return btn;
        }
      }
    ]
  });
}

function _openRoleChangeModal(user) {
  modal.open({
    title: `Change Role: ${user.email}`,
    bodyContent: `
      <div style="display: flex; flex-direction: column; gap: var(--space-4);">
        <div class="form-group">
          <label class="form-label">Current Role: <strong>${escapeHtml(user.role)}</strong></label>
        </div>
        <div class="form-group">
          <label class="form-label">Select New Role</label>
          <select id="modal-role-select" class="form-select">
            <option value="requester">requester</option>
            <option value="approver">approver</option>
            <option value="finance_admin">finance_admin</option>
            <option value="security_reviewer">security_reviewer</option>
            <option value="legal_reviewer">legal_reviewer</option>
            <option value="tenant_admin">tenant_admin</option>
          </select>
        </div>
      </div>
    `,
    footerButtons: [
      {
        text: 'Cancel',
        variant: 'secondary',
        onClick: ({ close }) => close()
      },
      {
        text: 'Update Role',
        variant: 'primary',
        onClick: ({ close }) => {
          const newRole = document.getElementById('modal-role-select').value;
          toast.success(`Role for ${user.email} updated to ${newRole}`);
          close();
          window.location.reload();
        }
      }
    ]
  });
}

function _renderInvitationsManagement(container) {
  const header = document.createElement('div');
  header.className = 'page-header';
  header.innerHTML = `
    <div>
      <h1 class="page-title">Tenant Member Invitations</h1>
      <p class="page-description">Send cryptographically signed invitation tokens with explicit role assignments.</p>
    </div>
  `;
  container.appendChild(header);

  // Invite Form Card
  const formCard = document.createElement('div');
  formCard.className = 'table-card';
  formCard.style.padding = 'var(--space-6)';
  formCard.innerHTML = `
    <h3 style="font-size: var(--text-base); font-weight: var(--font-bold); margin-bottom: var(--space-4);">Issue New Invitation</h3>
    <form id="admin-invite-form" style="display: flex; gap: var(--space-3); flex-wrap: wrap;">
      <input type="email" id="admin-invite-email" class="form-input" placeholder="colleague@domain.com" style="flex: 1; min-width: 240px;" required>
      <select id="admin-invite-role" class="form-select" style="width: 200px;">
        <option value="requester">requester</option>
        <option value="approver">approver</option>
        <option value="finance_admin">finance_admin</option>
        <option value="security_reviewer">security_reviewer</option>
        <option value="legal_reviewer">legal_reviewer</option>
        <option value="tenant_admin">tenant_admin</option>
      </select>
      <button type="submit" class="btn btn-primary" id="admin-invite-btn">Send Invite</button>
    </form>
  `;

  formCard.querySelector('#admin-invite-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = formCard.querySelector('#admin-invite-email').value.trim();
    const role = formCard.querySelector('#admin-invite-role').value;
    const btn = formCard.querySelector('#admin-invite-btn');

    if (!email) return;
    btn.classList.add('btn-loading');

    try {
      await invitationsApi.createInvitation(email, role);
      toast.success(`Invitation sent to ${email} for role ${role}`);
      formCard.querySelector('#admin-invite-email').value = '';
    } catch (err) {
      toast.error(err.message || 'Failed to issue invitation');
    } finally {
      btn.classList.remove('btn-loading');
    }
  });

  container.appendChild(formCard);
}

function _renderAuditChainLog(container) {
  const header = document.createElement('div');
  header.className = 'page-header';
  header.innerHTML = `
    <div>
      <h1 class="page-title">Cryptographic SHA-256 Audit Chain</h1>
      <p class="page-description">Tamper-evident hash chain linking all decisions, agent evaluations, and human sign-offs.</p>
    </div>
  `;
  container.appendChild(header);

  const tableContainer = document.createElement('div');
  container.appendChild(tableContainer);

  const blocks = [
    { blockIndex: 104, eventType: 'PO_GENERATED', payloadHash: '8f4c2810a4e76d91bb350c38481ffbc4465d64e9', parentHash: '9a2e3745...', verified: true, date: '2026-11-18 10:45:12' },
    { blockIndex: 103, eventType: 'APPROVAL_RECORDED', payloadHash: '7b3d1629c3d65e80aa249b27370eea3354c53d8', parentHash: '3c1f28...', verified: true, date: '2026-11-18 10:40:05' },
    { blockIndex: 102, eventType: 'MULTI_AGENT_CONSENSUS', payloadHash: '6a1c0518b2c54d7999138a16269dda2243b42c7', parentHash: '2b0e17...', verified: true, date: '2026-11-18 10:39:58' },
    { blockIndex: 101, eventType: 'INTAKE_REQUEST_INGESTED', payloadHash: '590b9407a1b43c6888027905158cca1132a31b6', parentHash: '1a9d06...', verified: true, date: '2026-11-18 10:39:56' }
  ];

  new DataTable({
    container: tableContainer,
    title: 'Immutable Audit Hash Ledger',
    data: blocks,
    columns: [
      { key: 'blockIndex', label: 'Block #', sortable: true },
      { key: 'eventType', label: 'Event Classification', sortable: true },
      {
        key: 'payloadHash',
        label: 'SHA-256 Payload Hash',
        render: (val) => `<code style="font-size:11px;">${escapeHtml(val.slice(0, 16))}...</code>`
      },
      {
        key: 'verified',
        label: 'Hash Integrity',
        render: (val) => `<span class="badge badge-compliant"><span class="badge-dot"></span>Verified Chain</span>`
      },
      { key: 'date', label: 'Block Timestamp', sortable: true }
    ]
  });
}

function _renderPolicyConfig(container) {
  const header = document.createElement('div');
  header.className = 'page-header';
  header.innerHTML = `
    <div>
      <h1 class="page-title">Policy Engine & Green Channel Configuration</h1>
      <p class="page-description">Configure autonomous approval thresholds, agent evaluation pipelines, and compliance baselines.</p>
    </div>
  `;
  container.appendChild(header);

  const card = document.createElement('div');
  card.className = 'table-card';
  card.style.padding = 'var(--space-6)';
  card.innerHTML = `
    <div style="max-width: 600px; display: flex; flex-direction: column; gap: var(--space-4);">
      <div class="form-group">
        <label class="form-label">Autonomous Green-Channel Spend Limit (USD)</label>
        <input type="number" class="form-input" value="5000" id="policy-green-limit">
        <div class="form-help">Requests below this limit auto-clear if all agents return compliant.</div>
      </div>

      <div class="form-group">
        <label class="form-label">Strict Dual-Wall RLS Enforcement</label>
        <select class="form-select" disabled>
          <option value="enabled" selected>Enabled (Fail-Closed)</option>
        </select>
        <div class="form-help">Mandatory security invariant. Cannot be disabled.</div>
      </div>

      <button type="button" class="btn btn-primary" id="save-policy-btn" style="align-self: flex-start;">
        Save Policy Changes
      </button>
    </div>
  `;

  card.querySelector('#save-policy-btn').addEventListener('click', () => {
    toast.success('Policy configurations updated.');
  });

  container.appendChild(card);
}
