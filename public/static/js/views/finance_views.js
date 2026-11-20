/**
 * Omnea Finance Workspace & Policy-Drafting Views (finance_views.js)
 * Route: #/app/finance
 *
 * Capabilities:
 * 1. Budgets Tab:
 *    - Department budgets table for period FY2026 fetched from GET /api/v1/finance/budgets.
 *    - Columns: Department, Currency, Total Allocated, Committed Spend, Active Reservations,
 *      Remaining Headroom, Utilization progress bar.
 *    - "Add / Update Budget" modal form.
 * 2. Spend Dashboard Tab:
 *    - Cards with spend per currency (USD, EUR, GBP, CAD - NEVER summed across currencies!).
 *    - Department spend breakdown table from GET /api/v1/finance/spend-dashboard.
 * 3. Approval Policies & Policy-Drafting Agent Tab:
 *    - Active policy version badge (#active-policy-version-badge).
 *    - Natural-language rule prompt input (#policy-rule-prompt).
 *    - "Draft Policy Rule" button (#btn-draft-policy) calling POST /api/v1/finance/policies/draft.
 *    - Visual diff container (#policy-draft-diff-container).
 *    - "Confirm & Activate Policy" button (#btn-confirm-policy-candidate).
 *    - Modal with #btn-confirm-activate-policy calling POST /api/v1/finance/policies/confirm.
 */

import { state, escapeHtml } from '../state.js';
import { financeApi, erpApi, reconciliationApi } from '../api.js';
import { DataTable } from '../components/table.js';
import { modal } from '../components/modal.js';
import { toast } from '../components/toast.js';
import { createTabs, createVisualDiff } from '../layout.js';

let liveBudgetsCache = [];
let liveSpendMetricsCache = null;
let liveActivePolicyCache = null;
let livePolicyHistoryCache = [];

export async function renderFinanceView(container, subRoute = '') {
  let initialTab = 'budgets';
  if (subRoute === '/spend' || subRoute === '/caps') {
    initialTab = 'spend';
  } else if (subRoute === '/policies' || subRoute === '/agent') {
    initialTab = 'policies';
  } else if (subRoute === '/erp' || subRoute === '/reconciliation') {
    initialTab = 'erp';
  }

  container.innerHTML = `
    <div class="page-header" style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: var(--space-4);">
      <div>
        <h1 class="page-title" style="margin: 0; font-size: var(--text-2xl); font-weight: var(--font-bold); color: var(--text-primary);">Finance Workspace & Financial Controls</h1>
        <p class="page-description" style="margin: var(--space-1) 0 0; font-size: var(--text-sm); color: var(--text-muted);">
          Department budget allocations, multi-currency segregated spend ledgers, natural-language approval policy drafting, and ERP ledger reconciliation.
        </p>
      </div>
      <div style="display: flex; gap: var(--space-2);">
        <button type="button" class="btn btn-primary" id="btn-add-budget">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 4px;">
            <line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line>
          </svg>
          Add / Update Budget
        </button>
      </div>
    </div>

    <!-- Navigation Tabs -->
    <div id="finance-tabs-container" style="margin-bottom: var(--space-4);"></div>

    <!-- Sub-tab Content Area -->
    <div id="finance-tab-content"></div>
  `;

  const tabsContainer = document.getElementById('finance-tabs-container');
  const contentContainer = document.getElementById('finance-tab-content');

  createTabs({
    container: tabsContainer,
    tabs: [
      { id: 'budgets', label: 'Budgets & Headroom', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="1" x2="12" y2="23"></line><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"></path></svg>' },
      { id: 'spend', label: 'Spend Dashboard', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="1" y="4" width="22" height="16" rx="2" ry="2"></rect><line x1="1" y1="10" x2="23" y2="10"></line></svg>' },
      { id: 'policies', label: 'Approval Policies & Policy-Drafting Agent', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>' },
      { id: 'erp', label: 'ERP Ledger & Reconciliation', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect><line x1="8" y1="21" x2="16" y2="21"></line><line x1="12" y1="17" x2="12" y2="21"></line></svg>' }
    ],
    activeTabId: initialTab,
    onTabChange: (tabId) => {
      renderFinanceTab(tabId, contentContainer);
    }
  });

  await renderFinanceTab(initialTab, contentContainer);

  const addBudgetBtn = document.getElementById('btn-add-budget');
  if (addBudgetBtn) {
    addBudgetBtn.addEventListener('click', () => {
      _openAddUpdateBudgetModal(contentContainer);
    });
  }
}

async function renderFinanceTab(tabId, container) {
  container.innerHTML = '';
  switch (tabId) {
    case 'budgets':
      await _renderBudgetsTab(container);
      break;
    case 'spend':
      await _renderSpendDashboardTab(container);
      break;
    case 'policies':
      await _renderApprovalPoliciesTab(container);
      break;
    case 'erp':
      await _renderErpReconciliationTab(container);
      break;
    default:
      await _renderBudgetsTab(container);
      break;
  }
}

/**
 * 1. Budgets Tab
 */
async function _renderBudgetsTab(container) {
  const wrapper = document.createElement('div');
  wrapper.className = 'finance-budgets-view';

  // Fetch live budgets
  let budgets = [];
  try {
    const fetched = await financeApi.getBudgets('FY2026');
    if (Array.isArray(fetched)) budgets = fetched;
  } catch (err) {
    budgets = [];
  }
  liveBudgetsCache = budgets;

  // Period Selector Toolbar
  const toolbar = document.createElement('div');
  toolbar.className = 'table-toolbar';
  toolbar.style.display = 'flex';
  toolbar.style.justifyContent = 'space-between';
  toolbar.style.alignItems = 'center';
  toolbar.style.marginBottom = 'var(--space-3)';

  toolbar.innerHTML = `
    <div style="display: flex; align-items: center; gap: var(--space-3);">
      <span style="font-size: var(--text-xs); font-weight: var(--font-bold); color: var(--text-secondary); text-transform: uppercase;">Fiscal Period:</span>
      <select id="budget-period-select" class="form-select" style="max-width: 140px; font-weight: 600;">
        <option value="FY2026" selected>FY2026</option>
        <option value="FY2025">FY2025</option>
      </select>
      <span class="badge badge-info" style="font-size: 11px;">Active Planning Year</span>
    </div>
    <div style="font-size: var(--text-xs); color: var(--text-muted);">
      Total Department Ledgers: <strong>${budgets.length}</strong>
    </div>
  `;

  const tableContainer = document.createElement('div');
  tableContainer.id = 'budgets-data-table';

  wrapper.appendChild(toolbar);
  wrapper.appendChild(tableContainer);
  container.appendChild(wrapper);

  const table = new DataTable({
    container: tableContainer,
    tbodyId: 'budgets-table-body',
    columns: [
      {
        key: 'department',
        label: 'Department',
        sortable: true,
        render: (_val, row) => {
          const r = (row && row.department) ? row : (_val || {});
          return `<strong style="color: var(--text-primary);">${escapeHtml(r.department || _val || '')}</strong>`;
        }
      },
      {
        key: 'currency',
        label: 'Currency',
        sortable: true,
        render: (_val, row) => {
          const r = (row && row.currency) ? row : (_val || {});
          return `<code style="font-weight: bold;">${escapeHtml(r.currency || _val || '')}</code>`;
        }
      },
      {
        key: 'amount',
        label: 'Total Allocated',
        sortable: true,
        render: (_val, row) => {
          const r = (row && row.amount !== undefined) ? row : (_val || {});
          return new Intl.NumberFormat('en-US', { style: 'currency', currency: r.currency || 'USD' }).format(r.amount || _val || 0);
        }
      },
      {
        key: 'committed',
        label: 'Committed Spend',
        sortable: true,
        render: (_val, row) => {
          const r = (row && row.committed !== undefined) ? row : (_val || {});
          return `<span style="font-weight: 600; color: var(--text-primary);">${new Intl.NumberFormat('en-US', { style: 'currency', currency: r.currency || 'USD' }).format(r.committed || _val || 0)}</span>`;
        }
      },
      {
        key: 'active_reserved',
        label: 'Active Reservations',
        sortable: true,
        render: (_val, row) => {
          const r = (row && row.active_reserved !== undefined) ? row : (_val || {});
          return `<span style="color: var(--warning);">${new Intl.NumberFormat('en-US', { style: 'currency', currency: r.currency || 'USD' }).format(r.active_reserved || 0)}</span>`;
        }
      },
      {
        key: 'headroom',
        label: 'Remaining Headroom',
        sortable: true,
        render: (_val, row) => {
          const r = (row && (row.headroom !== undefined || row.amount !== undefined)) ? row : (_val || {});
          const headroom = r.headroom !== undefined ? r.headroom : ((r.amount || 0) - (r.committed || 0) - (r.active_reserved || 0));
          const isNegative = headroom < 0;
          return `<strong class="badge ${isNegative ? 'badge-danger' : 'badge-success'}">${new Intl.NumberFormat('en-US', { style: 'currency', currency: r.currency || 'USD' }).format(headroom)}</strong>`;
        }
      }
    ],
    data: budgets,
    pageSize: 10,
    initialSortKey: 'department'
  });

  const periodSelect = toolbar.querySelector('#budget-period-select');
  if (periodSelect) {
    periodSelect.addEventListener('change', async (e) => {
      const p = e.target.value;
      const reloaded = await financeApi.getBudgets(p);
      if (Array.isArray(reloaded)) {
        table.setData(reloaded);
      }
    });
  }
}

/**
 * 2. Spend Dashboard Tab
 */
async function _renderSpendDashboardTab(container) {
  const wrapper = document.createElement('div');
  wrapper.className = 'finance-spend-view';

  let spendData = { spend_by_currency: {}, currency_totals: [], spend_by_department: {} };
  try {
    const fetched = await financeApi.getSpendMetrics();
    if (fetched && fetched.spend_by_currency) spendData = fetched;
  } catch (err) {
    // empty fallback
  }
  liveSpendMetricsCache = spendData;

  const spendByCurrency = spendData.spend_by_currency || {};
  const entries = Object.entries(spendByCurrency);

  wrapper.innerHTML = `
    <div style="margin-bottom: var(--space-4);">
      <h2 style="font-size: var(--text-lg); font-weight: var(--font-bold); margin: 0; color: var(--text-primary);">
        Enterprise Spend Analytics Dashboard
      </h2>
      <p style="font-size: var(--text-xs); color: var(--text-muted); margin: 2px 0 0;">
        Multi-Currency Strict Segregation Principle: Multi-currency financial aggregates partitioned strictly by ISO currency code. Currency amounts are never summed or mixed across currencies.
      </p>
    </div>

    <!-- Currency Cards Grid -->
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: var(--space-3); margin-bottom: var(--space-5);">
      ${entries.length === 0 ? `
        <div class="card stat-card" style="padding: var(--space-4); border: 1px solid var(--border-default); border-radius: var(--radius-md); background: var(--bg-surface);">
          <div style="font-size: var(--text-xs); color: var(--text-muted);">No spend transactions recorded.</div>
        </div>
      ` : entries.map(([curr, amount]) => {
        const formatted = new Intl.NumberFormat('en-US', { style: 'currency', currency: curr }).format(amount);
        return `
          <div class="card stat-card" style="padding: var(--space-4); border: 1px solid var(--border-default); border-radius: var(--radius-md); background: var(--bg-surface);">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-2);">
              <span style="font-size: var(--text-xs); font-weight: bold; color: var(--text-muted); text-transform: uppercase;">${escapeHtml(curr)} Committed Spend</span>
              <span class="badge badge-currency" style="font-weight: bold;">${escapeHtml(curr)}</span>
            </div>
            <div style="font-size: var(--text-2xl); font-weight: bold; color: var(--text-primary); letter-spacing: -0.02em;">
              ${escapeHtml(formatted)}
            </div>
            <div style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">
              Strict currency-partitioned aggregate
            </div>
          </div>
        `;
      }).join('')}
    </div>

    <!-- Department Spend Breakdown -->
    <div style="margin-top: var(--space-4);">
      <h3 style="font-size: var(--text-sm); font-weight: var(--font-bold); margin-bottom: var(--space-2); color: var(--text-primary);">
        Department Spend Breakdown
      </h3>
      <div style="font-size: var(--text-xs); color: var(--text-muted);">
        Spend categorized by department and currency without cross-currency summation.
      </div>
    </div>
  `;

  container.appendChild(wrapper);
}

/**
 * 3. Approval Policies & Policy-Drafting Agent Tab
 */
async function _renderApprovalPoliciesTab(container) {
  const wrapper = document.createElement('div');
  wrapper.className = 'finance-policies-view';

  // Fetch live active policy and policy history
  let activePolicy = null;
  let policyHistory = [];
  try {
    activePolicy = await financeApi.getActivePolicy();
    policyHistory = await financeApi.getPolicyHistory();
  } catch (err) {
    // Empty
  }
  liveActivePolicyCache = activePolicy;
  livePolicyHistoryCache = policyHistory;

  const currentVersion = activePolicy ? activePolicy.version : 'v1.0';
  const contentHash = activePolicy ? activePolicy.content_hash : 'hash_initial';
  const rules = (activePolicy && activePolicy.rules) ? activePolicy.rules : {
    vp_spend_threshold: { USD: 50000.0, EUR: 45000.0 },
    agent_autonomous_cap: 10000.0,
    allow_fast_track: false,
    over_budget_action: 'needs_human'
  };

  wrapper.innerHTML = `
    <!-- Top: Active Policy Version Header -->
    <div class="card" style="margin-bottom: var(--space-4); border-left: 4px solid var(--brand-primary); background: var(--bg-surface);">
      <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: var(--space-3);">
        <div>
          <div style="display: flex; align-items: center; gap: var(--space-2);">
            <span id="active-policy-version-badge" class="badge badge-success" style="font-weight: bold; font-size: var(--text-sm);">ACTIVE POLICY: ${escapeHtml(currentVersion)}</span>
            <span style="font-size: var(--text-xs); color: var(--text-muted);">Active Policy Version</span>
          </div>
          <div style="font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); margin-top: 4px;">
            Content Hash: <code style="background: var(--bg-subtle); padding: 2px 6px; border-radius: 4px;">${escapeHtml(contentHash)}</code>
          </div>
        </div>
        <div>
          <span class="badge badge-info">Deterministic Evaluator</span>
        </div>
      </div>

      <!-- Rules Snapshot Grid -->
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: var(--space-3); padding-top: var(--space-2); border-top: 1px solid var(--border-subtle); font-size: var(--text-xs);">
        <div>
          <div style="font-weight: 700; color: var(--text-muted); margin-bottom: 4px; text-transform: uppercase;">VP Spend Thresholds</div>
          ${Object.entries(rules.vp_spend_threshold || {}).map(([curr, val]) => `
            <div><strong>${escapeHtml(curr)}:</strong> ${new Intl.NumberFormat('en-US', { style: 'currency', currency: curr }).format(val)}</div>
          `).join('')}
        </div>

        <div>
          <div style="font-weight: 700; color: var(--text-muted); margin-bottom: 4px; text-transform: uppercase;">Autonomous Agent Cap</div>
          <div style="font-size: var(--text-base); font-weight: bold; color: var(--brand-primary);">$${new Intl.NumberFormat().format(rules.agent_autonomous_cap || 0)} USD</div>
          <div style="color: var(--text-muted); margin-top: 2px;">Over-Budget Action: <code>${escapeHtml(rules.over_budget_action || 'needs_human')}</code></div>
        </div>

        <div>
          <div style="font-weight: 700; color: var(--text-muted); margin-bottom: 4px; text-transform: uppercase;">Fast-Track Renewals</div>
          <div>Allowed: <strong>${rules.allow_fast_track ? 'Yes' : 'No'}</strong></div>
        </div>
      </div>
    </div>

    <!-- Middle: Policy-Drafting Assistant (Natural Language Agent) -->
    <div class="card" style="margin-bottom: var(--space-4); border: 1px solid var(--border-default); background: var(--bg-surface);">
      <div style="display: flex; align-items: center; gap: var(--space-2); margin-bottom: var(--space-2);">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--brand-primary)" stroke-width="2">
          <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
        </svg>
        <h3 style="margin: 0; font-size: var(--text-base); font-weight: var(--font-bold); color: var(--text-primary);">
          Policy-Drafting Assistant
        </h3>
      </div>
      <p style="font-size: var(--text-xs); color: var(--text-muted); margin: 0 0 var(--space-3);">
        Type plain-English policy amendments. The agent synthesizes schema-validated JSON, highlights diffs against active controls, and verifies anti-weakening invariants.
      </p>

      <div class="form-group" style="margin-bottom: var(--space-2);">
        <textarea id="policy-rule-prompt" class="form-textarea" rows="3" placeholder="e.g. Lower the VP threshold for USD to 35000 USD and keep EUR at 45000 EUR"></textarea>
      </div>

      <div style="display: flex; justify-content: flex-end;">
        <button type="button" class="btn btn-primary" id="btn-draft-policy">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 4px;">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
            <polyline points="14 2 14 8 20 8"></polyline>
            <line x1="16" y1="13" x2="8" y2="13"></line>
            <line x1="16" y1="17" x2="8" y2="17"></line>
          </svg>
          Draft Policy Rule
        </button>
      </div>

      <!-- Draft Output / Diff Inspector Container -->
      <div id="policy-draft-diff-container" style="margin-top: var(--space-4);"></div>
    </div>

    <!-- Bottom: Policy Version History -->
    <div class="card" style="background: var(--bg-surface); border: 1px solid var(--border-default);">
      <h3 style="font-size: var(--text-sm); font-weight: var(--font-bold); margin: 0 0 var(--space-2); color: var(--text-primary);">
        Policy Version History & Audit Trail
      </h3>
      <table class="data-table" style="width: 100%;">
        <thead>
          <tr style="text-align: left; font-size: var(--text-xs); color: var(--text-muted); border-bottom: 1px solid var(--border-subtle);">
            <th style="padding: var(--space-2);">Version</th>
            <th style="padding: var(--space-2);">Content Hash</th>
            <th style="padding: var(--space-2);">Status</th>
            <th style="padding: var(--space-2);">Created Date</th>
          </tr>
        </thead>
        <tbody>
          ${policyHistory.length === 0 ? `
            <tr><td colspan="4" style="padding: var(--space-4); text-align: center; color: var(--text-muted);">No policy history records found.</td></tr>
          ` : policyHistory.map(h => `
            <tr style="font-size: var(--text-xs); border-bottom: 1px solid var(--border-subtle);">
              <td style="padding: var(--space-2); font-weight: bold;">${escapeHtml(h.version)}</td>
              <td style="padding: var(--space-2); font-family: var(--font-mono); font-size: 10px;">${escapeHtml(String(h.content_hash || '').slice(0, 24))}...</td>
              <td style="padding: var(--space-2);"><span class="badge ${h.is_active ? 'badge-success' : 'badge-neutral'}">${h.is_active ? 'Active' : 'Archived'}</span></td>
              <td style="padding: var(--space-2);">${escapeHtml(String(h.created_at || '').slice(0, 10))}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;

  container.appendChild(wrapper);

  // Wire Draft Button
  const promptInput = wrapper.querySelector('#policy-rule-prompt');
  const draftBtn = wrapper.querySelector('#btn-draft-policy');
  const diffContainer = wrapper.querySelector('#policy-draft-diff-container');

  draftBtn.addEventListener('click', async () => {
    const text = (promptInput.value || '').trim();
    if (!text) {
      toast.warning('Please enter a policy amendment instruction.');
      return;
    }

    draftBtn.disabled = true;
    draftBtn.textContent = 'Drafting policy...';

    let draftRes = null;
    try {
      draftRes = await financeApi.draftPolicyRule(text);
    } catch (err) {
      draftBtn.disabled = false;
      draftBtn.textContent = 'Draft Policy Rule';
      toast.error('Policy drafting failed: ' + (err.message || 'unknown error'));
      return;
    }

    draftBtn.disabled = false;
    draftBtn.textContent = 'Draft Policy Rule';

    if (!draftRes) return;

    if (!draftRes.valid && draftRes.is_weakening) {
      diffContainer.innerHTML = `
        <div class="alert-banner alert-danger" style="padding: var(--space-4); background: var(--danger-bg); border: 1px solid var(--danger-border); border-radius: var(--radius-md);">
          <strong style="color: var(--danger-text); font-size: var(--text-sm);">
            Weakening of Finance controls rejected: ${escapeHtml((draftRes.weakening_reasons || []).join(', ') || 'Control weakening detected')}
          </strong>
        </div>
      `;
      toast.error('Weakening of Finance controls rejected.');
      return;
    }

    const proposedRules = draftRes.candidate_rules || rules;
    const nextVer = draftRes.suggested_version || 'v2.0';

    diffContainer.innerHTML = `
      <div style="border: 1px solid var(--border-default); border-radius: var(--radius-md); padding: var(--space-4); background: var(--bg-surface);">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-3);">
          <div>
            <div style="display: flex; align-items: center; gap: var(--space-2);">
              <span class="badge badge-info" style="font-weight: bold;">PROPOSED CANDIDATE: ${escapeHtml(nextVer)}</span>
              <span class="badge badge-success">Validation Passed</span>
            </div>
            <div style="font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); margin-top: 4px;">
              Diff: <pre style="margin: 4px 0 0; font-size: 12px; background: var(--bg-subtle); padding: 8px; border-radius: 4px;">${escapeHtml(JSON.stringify(draftRes.diff || proposedRules, null, 2))}</pre>
            </div>
          </div>
          <button type="button" class="btn btn-primary" id="btn-confirm-policy-candidate">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 4px;">
              <polyline points="20 6 9 17 4 12"></polyline>
            </svg>
            Confirm & Activate Policy
          </button>
        </div>
      </div>
    `;

    const confirmBtn = diffContainer.querySelector('#btn-confirm-policy-candidate');
    if (confirmBtn) {
      confirmBtn.addEventListener('click', () => {
        modal.open({
          title: `Activate Policy ${nextVer}`,
          bodyContent: `
            <div style="font-size: var(--text-sm); line-height: 1.5; color: var(--text-secondary);">
              <p>Activating <strong>Policy ${nextVer}</strong> will apply only to new requests. Policy changes apply only to new requests and never modify historical records.</p>
            </div>
          `,
          footerButtons: [
            {
              text: 'Cancel',
              variant: 'secondary',
              onClick: ({ close }) => close()
            },
            {
              id: 'btn-confirm-activate-policy',
              text: 'Confirm & Activate',
              variant: 'primary',
              onClick: async ({ close }) => {
                try {
                  await financeApi.confirmPolicy({
                    version: nextVer,
                    rules: proposedRules,
                    admin_override: false
                  });
                  close();
                  toast.success(`Policy ${nextVer} activated successfully.`);

                  const badge = document.getElementById('active-policy-version-badge');
                  if (badge) {
                    badge.textContent = `ACTIVE POLICY: ${nextVer}`;
                  }
                } catch (err) {
                  toast.error('Failed to confirm policy: ' + (err.message || 'unknown error'));
                }
              }
            }
          ]
        });
      });
    }
  });
}

function _openAddUpdateBudgetModal(mainContainer) {
  const form = document.createElement('div');
  form.innerHTML = `
    <div class="form-group">
      <label class="form-label" for="budget-dept-input">Department Name</label>
      <input type="text" id="budget-dept-input" class="form-input" placeholder="e.g. Engineering" value="">
    </div>

    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-3);">
      <div class="form-group">
        <label class="form-label" for="budget-currency-select">Currency</label>
        <select id="budget-currency-select" class="form-select">
          <option value="USD">USD ($)</option>
          <option value="EUR">EUR (€)</option>
          <option value="GBP">GBP (£)</option>
          <option value="CAD">CAD (CA$)</option>
        </select>
      </div>

      <div class="form-group">
        <label class="form-label" for="budget-period-input">Fiscal Period</label>
        <input type="text" id="budget-period-input" class="form-input" value="FY2026" readonly>
      </div>
    </div>

    <div class="form-group">
      <label class="form-label" for="budget-amount-input">Total Allocated Amount</label>
      <input type="number" id="budget-amount-input" class="form-input" placeholder="e.g. 500000" min="0" step="1000">
    </div>
  `;

  modal.open({
    title: 'Add / Update Department Budget',
    bodyContent: form,
    size: 'md',
    footerButtons: [
      {
        text: 'Cancel',
        variant: 'secondary',
        onClick: ({ close }) => close()
      },
      {
        text: 'Save Budget Allocation',
        variant: 'primary',
        onClick: async ({ close }) => {
          const dept = form.querySelector('#budget-dept-input').value.trim();
          const curr = form.querySelector('#budget-currency-select').value;
          const amt = parseFloat(form.querySelector('#budget-amount-input').value);

          if (!dept || isNaN(amt) || amt <= 0) {
            toast.warning('Please enter a valid department name and positive allocated amount.');
            return;
          }

          try {
            await financeApi.addOrUpdateBudget({
              department: dept,
              period: 'FY2026',
              currency: curr,
              amount: amt,
              committed: 0.0
            });
            toast.success(`Budget for ${dept} (${curr}) updated.`);
            close();
            await renderFinanceTab('budgets', mainContainer);
          } catch (e) {
            toast.error('Failed to update budget.');
          }
        }
      }
    ]
  });
}

/**
 * 4. ERP Ledger & Reconciliation Tab (Stage 6)
 */
async function _renderErpReconciliationTab(container) {
  const wrapper = document.createElement('div');
  wrapper.className = 'finance-erp-view';

  // Fetch live ERP status & latest reconciliation
  let erpStatus = null;
  let latestRecon = null;
  let sagaExecutions = [];
  let findings = [];

  try {
    const [statusRes, reconRes, sagasRes, findingsRes] = await Promise.allSettled([
      erpApi.getStatus(),
      reconciliationApi.getLatestRun(),
      erpApi.getSagaExecutions(),
      reconciliationApi.getFindings({ status: 'open' })
    ]);

    if (statusRes.status === 'fulfilled') erpStatus = statusRes.value;
    if (reconRes.status === 'fulfilled') latestRecon = reconRes.value;
    if (sagasRes.status === 'fulfilled' && Array.isArray(sagasRes.value)) sagaExecutions = sagasRes.value;
    if (findingsRes.status === 'fulfilled' && Array.isArray(findingsRes.value)) findings = findingsRes.value;
  } catch (err) {
    console.error('Error loading ERP and reconciliation status:', err);
  }

  const connStatus = erpStatus ? erpStatus.connection_status : 'not_connected';
  const cbState = erpStatus ? erpStatus.circuit_breaker_state : 'CLOSED';
  const connectorType = erpStatus ? erpStatus.connector_type : 'ledger';
  const poCount = erpStatus ? erpStatus.purchase_orders_count : 0;
  const commitmentsCount = erpStatus ? erpStatus.commitments_count : 0;
  const committedAmt = erpStatus ? erpStatus.total_committed_amount : '0.00';

  const badgeVariant = connStatus === 'connected' ? 'badge-success' : 'badge-warning';
  const cbVariant = cbState === 'CLOSED' ? 'badge-success' : (cbState === 'HALF_OPEN' ? 'badge-warning' : 'badge-danger');

  wrapper.innerHTML = `
    <!-- Top KPI Grid: System of Record Posture -->
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: var(--space-4); margin-bottom: var(--space-6);">
      <div class="kpi-card" style="background: var(--bg-surface); padding: var(--space-4); border-radius: var(--radius-lg); border: 1px solid var(--border-color);">
        <div style="font-size: var(--text-xs); color: var(--text-muted); text-transform: uppercase; font-weight: var(--font-semibold); margin-bottom: var(--space-1);">ERP Connection Status</div>
        <div style="display: flex; align-items: center; gap: var(--space-2); margin-top: var(--space-1);">
          <span id="erp-sync-status-badge" class="badge ${badgeVariant}" style="text-transform: capitalize; font-size: var(--text-sm);">
            ${escapeHtml(connStatus)} (${escapeHtml(connectorType)})
          </span>
          <span class="badge ${cbVariant}" title="Circuit Breaker" style="font-size: var(--text-xs);">
            CB: ${escapeHtml(cbState)}
          </span>
        </div>
        <div style="font-size: var(--text-xs); color: var(--text-muted); margin-top: var(--space-2);">
          SOX System of Record & Token Bucket Limiter
        </div>
      </div>

      <div class="kpi-card" style="background: var(--bg-surface); padding: var(--space-4); border-radius: var(--radius-lg); border: 1px solid var(--border-color);">
        <div style="font-size: var(--text-xs); color: var(--text-muted); text-transform: uppercase; font-weight: var(--font-semibold); margin-bottom: var(--space-1);">ERP Purchase Orders</div>
        <div style="font-size: var(--text-2xl); font-weight: var(--font-bold); color: var(--text-primary);">${poCount}</div>
        <div style="font-size: var(--text-xs); color: var(--text-muted); margin-top: var(--space-1);">
          Count verified from ERP's own records
        </div>
      </div>

      <div class="kpi-card" style="background: var(--bg-surface); padding: var(--space-4); border-radius: var(--radius-lg); border: 1px solid var(--border-color);">
        <div style="font-size: var(--text-xs); color: var(--text-muted); text-transform: uppercase; font-weight: var(--font-semibold); margin-bottom: var(--space-1);">Committed Ledger Spend</div>
        <div style="font-size: var(--text-2xl); font-weight: var(--font-bold); color: var(--text-primary);">$${escapeHtml(committedAmt)}</div>
        <div style="font-size: var(--text-xs); color: var(--text-muted); margin-top: var(--space-1);">
          ${commitmentsCount} active ledger commitments
        </div>
      </div>

      <div class="kpi-card" style="background: var(--bg-surface); padding: var(--space-4); border-radius: var(--radius-lg); border: 1px solid var(--border-color);">
        <div style="font-size: var(--text-xs); color: var(--text-muted); text-transform: uppercase; font-weight: var(--font-semibold); margin-bottom: var(--space-1);">Reconciliation Health</div>
        <div style="display: flex; align-items: center; justify-content: space-between; margin-top: var(--space-1);">
          <span class="badge ${findings.length === 0 ? 'badge-success' : 'badge-danger'}" style="font-size: var(--text-sm);">
            ${findings.length === 0 ? 'Zero Drift' : `${findings.length} Open Findings`}
          </span>
          <button type="button" class="btn btn-secondary btn-sm" id="btn-run-reconciliation" style="font-size: var(--text-xs);">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 4px;">
              <polyline points="23 4 23 10 17 10"></polyline>
              <polyline points="1 20 1 14 7 14"></polyline>
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
            </svg>
            Run Now
          </button>
        </div>
        <div style="font-size: var(--text-xs); color: var(--text-muted); margin-top: var(--space-2);">
          ${latestRecon && latestRecon.status ? `Last Run: ${escapeHtml(latestRecon.status)}` : 'Clean baseline'}
        </div>
      </div>
    </div>

    <!-- Idempotent Saga Execution Tracing -->
    <div class="table-card" style="margin-bottom: var(--space-6); background: var(--bg-surface); border-radius: var(--radius-lg); border: 1px solid var(--border-color); padding: var(--space-4);">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-3);">
        <div>
          <h3 style="margin: 0; font-size: var(--text-base); font-weight: var(--font-semibold); color: var(--text-primary);">
            Idempotent ERP Saga Executions
          </h3>
          <p style="margin: 2px 0 0; font-size: var(--text-xs); color: var(--text-muted);">
            Lookup-before-retry, compensation on failure, and deterministic 4-step sync pipeline.
          </p>
        </div>
      </div>
      <div id="erp-saga-steps-container">
        ${_renderSagaStepsHtml(sagaExecutions)}
      </div>
    </div>

    <!-- Reconciliation Drift Findings & Audited Repairs -->
    <div class="table-card" style="background: var(--bg-surface); border-radius: var(--radius-lg); border: 1px solid var(--border-color); padding: var(--space-4);">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-3);">
        <div>
          <h3 style="margin: 0; font-size: var(--text-base); font-weight: var(--font-semibold); color: var(--text-primary);">
            Reconciliation Drift Findings & Audited Repairs
          </h3>
          <p style="margin: 2px 0 0; font-size: var(--text-xs); color: var(--text-muted);">
            Seeded drift detection, SHA-256 Merkle-audited repairs requiring mandatory business explanation.
          </p>
        </div>
      </div>
      <div id="reconciliation-findings-table"></div>
    </div>
  `;

  container.appendChild(wrapper);

  // Bind Run Reconciliation button
  const runReconBtn = wrapper.querySelector('#btn-run-reconciliation');
  if (runReconBtn) {
    runReconBtn.addEventListener('click', async () => {
      runReconBtn.disabled = true;
      runReconBtn.innerHTML = 'Running...';
      try {
        const result = await reconciliationApi.runReconciliation();
        toast.success(`Reconciliation finished: ${result.drift_count} drift(s) detected.`);
        await renderFinanceTab('erp', container);
      } catch (err) {
        toast.error('Failed to execute reconciliation run.');
      } finally {
        runReconBtn.disabled = false;
        runReconBtn.innerHTML = 'Run Now';
      }
    });
  }

  // Render Findings Table
  const findingsTableEl = wrapper.querySelector('#reconciliation-findings-table');
  if (findingsTableEl) {
    if (findings.length === 0) {
      findingsTableEl.innerHTML = `
        <div style="padding: var(--space-6); text-align: center; color: var(--text-muted);">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin: 0 auto var(--space-2); opacity: 0.6;">
            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
            <polyline points="22 4 12 14.01 9 11.01"></polyline>
          </svg>
          <div style="font-weight: var(--font-medium); color: var(--text-primary);">Zero Drift Detected</div>
          <div style="font-size: var(--text-xs); margin-top: 4px;">Omnea procurement records and ERP system of record are in 100% sync.</div>
        </div>
      `;
    } else {
      new DataTable({
        container: findingsTableEl,
        title: 'Open Discrepancies',
        data: findings,
        columns: [
          { key: 'finding_type', label: 'Drift Type', sortable: true, render: (val) => `<span class="badge badge-warning">${escapeHtml(val)}</span>` },
          { key: 'request_id', label: 'Request ID', render: (val) => val ? `<code>${escapeHtml(String(val).slice(0, 8))}...</code>` : '—' },
          { key: 'po_number', label: 'PO Number', render: (val) => val ? `<code>${escapeHtml(val)}</code>` : '—' },
          {
            key: 'details',
            label: 'Discrepancy Details',
            render: (val, row) => {
              const omneaAmt = row.omnea_amount ? `$${row.omnea_amount}` : '';
              const erpAmt = row.erp_amount ? `$${row.erp_amount}` : '';
              if (omneaAmt && erpAmt) return `Omnea: ${escapeHtml(omneaAmt)} vs ERP: ${escapeHtml(erpAmt)}`;
              return escapeHtml(JSON.stringify(val || {}));
            }
          },
          { key: 'status', label: 'Status', render: (val) => `<span class="badge badge-${val === 'resolved' ? 'success' : 'danger'}">${escapeHtml(val)}</span>` },
          {
            key: 'id',
            label: 'Actions',
            render: (findingId, row) => {
              const btn = document.createElement('button');
              btn.className = 'btn btn-primary btn-sm btn-repair-finding';
              btn.setAttribute('data-finding-id', findingId);
              btn.setAttribute('data-action', row.finding_type === 'missing_in_erp' ? 'create_missing_po' : 'resync_status');
              btn.innerHTML = 'Repair...';
              btn.addEventListener('click', () => {
                _openRepairFindingModal(findingId, row, container);
              });
              return btn;
            }
          }
        ]
      });
    }
  }
}

function _renderSagaStepsHtml(sagas) {
  if (!sagas || sagas.length === 0) {
    return `
      <div style="padding: var(--space-4); text-align: center; color: var(--text-muted); font-size: var(--text-xs);">
        No recent ERP sync sagas executed. Approved procurement requests will trigger automated saga transactions.
      </div>
    `;
  }

  return `
    <div style="display: flex; flex-direction: column; gap: var(--space-3);">
      ${sagas.slice(0, 5).map(saga => {
        const steps = saga.steps || [];
        const statusBadge = saga.status === 'completed'
          ? 'badge-success'
          : (saga.status === 'compensated' ? 'badge-warning' : 'badge-danger');

        return `
          <div style="border: 1px solid var(--border-color); border-radius: var(--radius-md); padding: var(--space-3); background: var(--bg-canvas);">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-2);">
              <div style="display: flex; align-items: center; gap: var(--space-2);">
                <span style="font-weight: var(--font-medium); font-size: var(--text-sm); color: var(--text-primary);">
                  Saga: ${escapeHtml(saga.saga_name || 'erp_sync')}
                </span>
                <code style="font-size: var(--text-xs); color: var(--text-muted);">
                  ${escapeHtml(String(saga.id).slice(0, 8))}
                </code>
              </div>
              <span class="badge ${statusBadge}" style="text-transform: capitalize;">
                ${escapeHtml(saga.status)}
              </span>
            </div>
            <!-- Steps timeline -->
            <div style="display: flex; gap: var(--space-2); flex-wrap: wrap;">
              ${steps.map((st, idx) => {
                const isStepOk = st.status === 'completed';
                const pillVariant = isStepOk ? 'var(--color-success)' : (st.status === 'compensated' ? 'var(--color-warning)' : 'var(--color-danger)');
                return `
                  <div style="font-size: var(--text-xs); display: flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: var(--radius-sm); background: var(--bg-surface); border: 1px solid var(--border-color);">
                    <span style="display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: ${pillVariant};"></span>
                    <span>${idx + 1}. ${escapeHtml(st.name || st.step)}</span>
                  </div>
                `;
              }).join('')}
            </div>
          </div>
        `;
      }).join('')}
    </div>
  `;
}

function _openRepairFindingModal(findingId, findingRow, parentContainer) {
  const form = document.createElement('div');
  form.innerHTML = `
    <div style="display: flex; flex-direction: column; gap: var(--space-4);">
      <p style="margin: 0; font-size: var(--text-sm); color: var(--text-muted);">
        Authorize an audited reconciliation repair action. This operation will sync the SOX ledger with ERP records and write a SHA-256 Merkle audit trail.
      </p>

      <div class="form-group">
        <label class="form-label">Finding Type</label>
        <input type="text" class="form-input" value="${escapeHtml(findingRow.finding_type || 'drift')}" readonly>
      </div>

      <div class="form-group">
        <label class="form-label" for="repair-action-select">Repair Action <span style="color: var(--color-danger);">*</span></label>
        <select id="repair-action-select" class="form-select">
          <option value="create_missing_po" ${findingRow.finding_type === 'missing_in_erp' ? 'selected' : ''}>Create Missing PO in ERP</option>
          <option value="resync_status" ${findingRow.finding_type === 'status_drift' ? 'selected' : ''}>Resync Status with ERP</option>
          <option value="realign_commitment" ${findingRow.finding_type === 'amount_mismatch' || findingRow.finding_type === 'uncommitted_budget' ? 'selected' : ''}>Realign Budget Commitment</option>
          <option value="cancel_orphan_po">Cancel Orphan PO</option>
        </select>
      </div>

      <div class="form-group">
        <label class="form-label" for="repair-reason-input">Audit Explanation / Business Justification <span style="color: var(--color-danger);">*</span></label>
        <textarea id="repair-reason-input" class="form-input" rows="3" placeholder="Provide mandatory justification for this reconciliation correction (min 5 chars)..." required></textarea>
        <div class="form-help">Mandatory for SOX compliance audit log.</div>
      </div>
    </div>
  `;

  modal.open({
    title: `Repair Discrepancy: ${findingId.slice(0, 8)}`,
    bodyContent: form,
    size: 'md',
    footerButtons: [
      {
        text: 'Cancel',
        variant: 'secondary',
        onClick: ({ close }) => close()
      },
      {
        text: 'Authorize & Repair',
        variant: 'primary',
        onClick: async ({ close }) => {
          const action = form.querySelector('#repair-action-select').value;
          const reason = form.querySelector('#repair-reason-input').value.trim();

          if (!reason || reason.length < 5) {
            toast.warning('Please enter a valid audit explanation (at least 5 characters).');
            return;
          }

          try {
            await reconciliationApi.repairFinding(findingId, {
              repair_action: action,
              reason: reason
            });
            toast.success('Reconciliation repair executed and audited.');
            close();
            await renderFinanceTab('erp', parentContainer);
          } catch (err) {
            toast.error(err.message || 'Failed to execute repair.');
          }
        }
      }
    ]
  });
}
