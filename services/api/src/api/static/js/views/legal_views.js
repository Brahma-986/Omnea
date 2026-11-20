/**
 * Omnea Legal Workspace & Contract Risk Views (legal_views.js)
 * Route: #/app/legal
 *
 * Capabilities:
 * 1. Contracts Directory:
 *    - Search input and filter pills (All, Active, Expired, Auto-Renew, MSA, DPA).
 *    - Columns: Title, Vendor, Status, Type, Total Value & Currency, Auto-Renew status, Expiration Date.
 * 2. Contract Detail View / Modal:
 *    - Contract header, start/end date, renewal notice period (e.g. 60 days).
 *    - Flagged Clauses Inspector:
 *      * Cards for each flagged clause: Clause Type, Risk Level (High, Medium, Low),
 *        Page number, Verbatim Quote, Legal Recommendation.
 *      * Button to flag / add new clause with quote and page number.
 */

import { state, escapeHtml } from '../state.js';
import { legalApi } from '../api.js';
import { DataTable } from '../components/table.js';
import { modal } from '../components/modal.js';
import { toast } from '../components/toast.js';
import { openDetailDrawer } from '../layout.js';

let liveContractsCache = [];

export async function renderLegalView(container, subRoute = '') {
  let activeFilter = 'All';

  container.innerHTML = `
    <div class="page-header" style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: var(--space-4);">
      <div>
        <h1 class="page-title" style="margin: 0; font-size: var(--text-2xl); font-weight: var(--font-bold); color: var(--text-primary);">Legal Workspace & Contract Risk Review</h1>
        <p class="page-description" style="margin: var(--space-1) 0 0; font-size: var(--text-sm); color: var(--text-muted);">
          Contract repository, auto-renewal notice tracking, and verbatim clause risk inspector.
        </p>
      </div>
      <div style="display: flex; gap: var(--space-2);">
        <button type="button" class="btn btn-secondary" id="btn-export-contracts">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 4px;">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
            <polyline points="7 10 12 15 17 10"></polyline>
            <line x1="12" y1="15" x2="12" y2="3"></line>
          </svg>
          Export Contracts
        </button>
      </div>
    </div>

    <!-- Filter Pills Toolbar -->
    <div class="card" style="margin-bottom: var(--space-4); padding: var(--space-3); background: var(--bg-surface); border: 1px solid var(--border-default);">
      <div style="display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: var(--space-3);">
        <!-- Filter Pills -->
        <div class="filter-pills-group" style="display: flex; flex-wrap: wrap; gap: var(--space-2);" role="group" aria-label="Contract filter pills">
          ${['All', 'Active', 'Expired', 'Auto-Renew', 'MSA', 'DPA'].map(f => `
            <button type="button" class="btn btn-sm ${f === activeFilter ? 'btn-primary' : 'btn-ghost'} filter-pill-btn" data-filter="${f}">
              ${escapeHtml(f)}
            </button>
          `).join('')}
        </div>

        <!-- Search Bar -->
        <div style="min-width: 260px; max-width: 350px;">
          <input type="text" id="contract-search-input" class="form-input" placeholder="Search contracts, vendors, keys..." aria-label="Search contracts">
        </div>
      </div>
    </div>

    <!-- Contracts Data Table Container -->
    <div id="contracts-table-container"></div>
  `;

  const tableContainer = document.getElementById('contracts-table-container');

  // Fetch live contracts from authoritative backend API
  let contracts = [];
  try {
    const fetched = await legalApi.getContracts();
    if (Array.isArray(fetched)) {
      contracts = fetched;
    }
  } catch (err) {
    contracts = [];
  }
  liveContractsCache = contracts;

  // Initialize Data Table
  const table = new DataTable({
    container: tableContainer,
    tbodyId: 'contracts-table-body',
    columns: [
      {
        key: 'title',
        label: 'Title',
        sortable: true,
        render: (_val, row) => {
          const r = (row && row.id) ? row : (_val || {});
          return `
            <div>
              <div style="font-weight: var(--font-semibold); color: var(--text-primary); cursor: pointer;" class="contract-link" data-id="${r.id}" data-contract-id="${r.id}">
                ${escapeHtml(r.title || '')}
              </div>
              <div style="font-size: 11px; color: var(--text-muted);">ID: <code>${escapeHtml(String(r.id || ''))}</code></div>
            </div>
          `;
        }
      },
      {
        key: 'vendor_name',
        label: 'Vendor',
        sortable: true,
        render: (_val, row) => {
          const r = (row && row.id) ? row : (_val || {});
          return `
            <div>
              <strong style="color: var(--text-primary);">${escapeHtml(r.vendor_name || 'Vendor')}</strong>
            </div>
          `;
        }
      },
      {
        key: 'status',
        label: 'Status',
        sortable: true,
        render: (_val, row) => {
          const r = (row && row.id) ? row : (_val || {});
          if (r.status === 'active') return `<span class="badge badge-success">Active</span>`;
          if (r.status === 'expired') return `<span class="badge badge-muted">Expired</span>`;
          return `<span class="badge badge-neutral">${escapeHtml(r.status || '')}</span>`;
        }
      },
      {
        key: 'type',
        label: 'Type',
        sortable: true,
        render: (_val, row) => {
          const r = (row && row.id) ? row : (_val || {});
          const typeName = r.msa_status ? 'MSA' : (r.dpa_status ? 'DPA' : (r.type || 'Contract'));
          return `<span class="badge badge-info" style="font-weight: bold;">${escapeHtml(typeName)}</span>`;
        }
      },
      {
        key: 'total_value',
        label: 'Total Value & Currency',
        sortable: true,
        render: (_val, row) => {
          const r = (row && row.id) ? row : (_val || {});
          return `<strong class="badge badge-currency">${new Intl.NumberFormat('en-US', { style: 'currency', currency: r.currency || 'USD' }).format(r.total_value || 0)}</strong>`;
        }
      },
      {
        key: 'auto_renew',
        label: 'Auto-Renew Status',
        sortable: true,
        render: (_val, row) => {
          const r = (row && row.id) ? row : (_val || {});
          if (r.auto_renew) {
            const notice = r.renewal_terms || 'notice required';
            return `<span class="badge badge-warning" title="${escapeHtml(notice)}">Auto-Renews</span>`;
          }
          return `<span class="badge badge-ghost">Non-Renewing</span>`;
        }
      },
      {
        key: 'end_date',
        label: 'Expiration Date',
        sortable: true,
        render: (_val, row) => {
          const r = (row && row.id) ? row : (_val || {});
          return `<span style="font-size: var(--text-xs);">${escapeHtml(r.end_date || 'N/A')}</span>`;
        }
      },
      {
        key: 'actions',
        label: 'Actions',
        sortable: false,
        render: (_val, row) => {
          const r = (row && row.id) ? row : (_val || {});
          return `
            <button type="button" class="btn btn-secondary btn-sm inspect-contract-btn" data-id="${r.id}" data-contract-id="${r.id}">
              Inspect Clauses
            </button>
          `;
        }
      }
    ],
    data: contracts,
    pageSize: 10,
    initialSortKey: 'title'
  });

  // Filter & Search Logic
  const filterButtons = container.querySelectorAll('.filter-pill-btn');
  const searchInput = container.querySelector('#contract-search-input');

  const applyContractFilters = () => {
    const q = (searchInput.value || '').toLowerCase().trim();

    const filtered = liveContractsCache.filter(c => {
      const matchSearch = !q ||
        (c.title && c.title.toLowerCase().includes(q)) ||
        (c.vendor_name && c.vendor_name.toLowerCase().includes(q));

      let matchFilter = true;
      if (activeFilter === 'Active') matchFilter = c.status === 'active';
      else if (activeFilter === 'Expired') matchFilter = c.status === 'expired';
      else if (activeFilter === 'Auto-Renew') matchFilter = Boolean(c.auto_renew);
      else if (activeFilter === 'MSA') matchFilter = Boolean(c.msa_status || c.type === 'MSA');
      else if (activeFilter === 'DPA') matchFilter = Boolean(c.dpa_status || c.type === 'DPA');

      return matchSearch && matchFilter;
    });

    table.setData(filtered);
    _attachContractActionHandlers(tableContainer);
  };

  filterButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      filterButtons.forEach(b => {
        b.classList.remove('btn-primary');
        b.classList.add('btn-ghost');
      });
      btn.classList.remove('btn-ghost');
      btn.classList.add('btn-primary');
      activeFilter = btn.getAttribute('data-filter');
      applyContractFilters();
    });
  });

  searchInput.addEventListener('input', applyContractFilters);
  _attachContractActionHandlers(tableContainer);

  const exportBtn = document.getElementById('btn-export-contracts');
  if (exportBtn) {
    exportBtn.addEventListener('click', () => {
      table.exportCsv();
      toast.success('Contracts export generated.');
    });
  }
}

function _attachContractActionHandlers(container) {
  const buttons = container.querySelectorAll('.inspect-contract-btn, .contract-link');
  buttons.forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const id = btn.getAttribute('data-id') || btn.getAttribute('data-contract-id');
      await _openContractDetailDrawer(id);
    });
  });
}

/**
 * Contract Detail & Flagged Clauses Inspector Modal / Drawer
 */
async function _openContractDetailDrawer(contractId) {
  let contract = null;
  try {
    contract = await legalApi.getContract(contractId);
  } catch (err) {
    contract = liveContractsCache.find(c => c.id === contractId);
  }

  if (!contract) {
    toast.error('Contract details could not be loaded.');
    return;
  }

  const content = document.createElement('div');
  content.style.display = 'flex';
  content.style.flexDirection = 'column';
  content.style.gap = 'var(--space-4)';

  const flaggedClauses = contract.flagged_clauses || [];

  content.innerHTML = `
    <!-- Contract Header & Summary Card -->
    <div class="card" style="background: var(--bg-subtle); padding: var(--space-3); border-radius: var(--radius-sm);">
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-2); font-size: var(--text-xs);">
        <div><strong style="color: var(--text-muted);">Vendor:</strong> <span style="font-weight: 600;">${escapeHtml(contract.vendor_name || 'Vendor')}</span></div>
        <div><strong style="color: var(--text-muted);">Contract Type:</strong> <span class="badge badge-info">${escapeHtml(contract.msa_status ? 'MSA' : (contract.dpa_status ? 'DPA' : (contract.type || 'Contract')))}</span></div>
        <div><strong style="color: var(--text-muted);">Total Value:</strong> <span class="badge badge-currency">${new Intl.NumberFormat('en-US', { style: 'currency', currency: contract.currency || 'USD' }).format(contract.total_value || 0)}</span></div>
        <div><strong style="color: var(--text-muted);">Status:</strong> <span class="badge ${contract.status === 'active' ? 'badge-success' : 'badge-muted'}">${escapeHtml(contract.status)}</span></div>
        <div><strong style="color: var(--text-muted);">Start Date:</strong> <span>${escapeHtml(contract.start_date || 'N/A')}</span></div>
        <div><strong style="color: var(--text-muted);">Expiration Date:</strong> <span>${escapeHtml(contract.end_date || 'N/A')}</span></div>
        <div><strong style="color: var(--text-muted);">Renewal Notice Period:</strong> <span class="badge badge-warning">${escapeHtml(contract.renewal_notice_terms || contract.renewal_terms || (contract.auto_renew ? 'Auto-renews' : 'Non-renewing'))}</span></div>
        <div><strong style="color: var(--text-muted);">MSA / DPA Status:</strong> <span>MSA: ${escapeHtml(contract.msa_status || 'N/A')} | DPA: ${escapeHtml(contract.dpa_status || 'N/A')}</span></div>
      </div>
    </div>

    <!-- Flagged Clauses Inspector Header -->
    <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid var(--border-subtle); padding-bottom: var(--space-2);">
      <div>
        <h4 style="font-size: var(--text-sm); font-weight: var(--font-bold); margin: 0; color: var(--text-primary);">
          Flagged Clauses Inspector (${flaggedClauses.length})
        </h4>
        <p style="font-size: 11px; color: var(--text-muted); margin: 2px 0 0;">
          Legal obligations, uncapped liability, and compliance restrictions extracted from document text.
        </p>
      </div>
      <button type="button" class="btn btn-primary btn-sm" id="btn-add-clause-modal">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 4px;">
          <line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line>
        </svg>
        Flag / Add Clause
      </button>
    </div>

    <!-- Flagged Clauses Cards List -->
    <div id="contract-clauses-list" style="display: flex; flex-direction: column; gap: var(--space-3);">
      ${_renderClausesCards(flaggedClauses)}
    </div>
  `;

  openDetailDrawer({
    id: 'contract-detail-drawer',
    title: contract.title,
    subtitle: `Vendor: ${contract.vendor_name || 'Vendor'} | ID: ${contract.id}`,
    content,
    actions: [
      {
        label: 'Close',
        variant: 'secondary',
        onClick: ({ close }) => close()
      }
    ]
  });

  const addClauseBtn = content.querySelector('#btn-add-clause-modal');
  if (addClauseBtn) {
    addClauseBtn.addEventListener('click', () => {
      _openAddClauseModal(contract, content);
    });
  }
}

function _renderClausesCards(clauses = []) {
  if (!clauses || clauses.length === 0) {
    return `
      <div style="padding: var(--space-6); text-align: center; color: var(--text-muted); font-size: var(--text-xs); border: 1px dashed var(--border-default); border-radius: var(--radius-sm);">
        No flagged high-risk clauses identified in this contract.
      </div>
    `;
  }

  return clauses.map(clause => {
    const risk = (clause.risk_level || 'medium').toLowerCase();
    let riskBadgeClass = 'badge-info';
    if (risk === 'high' || risk === 'critical') riskBadgeClass = 'badge-danger';
    else if (risk === 'medium') riskBadgeClass = 'badge-warning';

    const clauseType = clause.clause_type || clause.type || 'Flagged Term';
    const pageStr = clause.page ? (String(clause.page).startsWith('Page') ? clause.page : `Page ${clause.page}`) : 'Document Body';

    return `
      <div class="card clause-card" style="border: 1px solid var(--border-default); border-radius: var(--radius-md); padding: var(--space-3); background: var(--bg-surface);">
        <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: var(--space-2);">
          <div style="display: flex; align-items: center; gap: var(--space-2);">
            <strong style="font-size: var(--text-sm); color: var(--text-primary);">${escapeHtml(clauseType)}</strong>
            <span class="badge ${riskBadgeClass}" style="font-weight: bold; font-size: 10px; text-transform: capitalize;">${escapeHtml(clause.risk_level || 'medium')} Risk</span>
          </div>
          <span style="font-size: 11px; font-weight: 600; color: var(--text-muted); background: var(--bg-subtle); padding: 2px 6px; border-radius: 4px;">
            ${escapeHtml(pageStr)}
          </span>
        </div>

        <!-- Verbatim Quote Snippet -->
        <div class="quote-snippet" style="background: var(--bg-subtle); border-left: 3px solid var(--brand-primary); padding: var(--space-2) var(--space-3); font-size: var(--text-xs); font-style: italic; color: var(--text-secondary); margin-bottom: var(--space-2); border-radius: 0 4px 4px 0;">
          &ldquo;${escapeHtml(clause.quote)}&rdquo;
        </div>

        <!-- Legal Recommendation -->
        <div style="font-size: var(--text-xs); background: var(--brand-subtle); border: 1px solid var(--brand-border); padding: var(--space-2) var(--space-3); border-radius: var(--radius-sm); color: var(--brand-primary);">
          <strong>Legal Recommendation:</strong> ${escapeHtml(clause.recommendation || 'Review with legal counsel.')}
        </div>
      </div>
    `;
  }).join('');
}

function _openAddClauseModal(contract, drawerContent) {
  const form = document.createElement('div');
  form.innerHTML = `
    <div class="form-group">
      <label class="form-label" for="clause-type-select">Clause Type</label>
      <select id="clause-type-select" class="form-select">
        <option value="Limitation of Liability">Limitation of Liability</option>
        <option value="Indemnity Cap">Indemnity Cap</option>
        <option value="Governing Law">Governing Law</option>
        <option value="Data Transfer">Data Transfer</option>
        <option value="Intellectual Property">Intellectual Property Ownership</option>
        <option value="Termination for Convenience">Termination for Convenience</option>
        <option value="Audit & Inspection">Audit & Inspection Rights</option>
      </select>
    </div>

    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-3);">
      <div class="form-group">
        <label class="form-label" for="clause-risk-select">Risk Level</label>
        <select id="clause-risk-select" class="form-select">
          <option value="high">High Risk</option>
          <option value="medium">Medium Risk</option>
          <option value="low">Low Risk</option>
        </select>
      </div>

      <div class="form-group">
        <label class="form-label" for="clause-page-input">Page & Section</label>
        <input type="text" id="clause-page-input" class="form-input" placeholder="e.g. Page 12, Section 8.1">
      </div>
    </div>

    <div class="form-group">
      <label class="form-label" for="clause-quote-textarea">Verbatim Quote</label>
      <textarea id="clause-quote-textarea" class="form-textarea" rows="3" placeholder="Paste exact contractual wording here..."></textarea>
    </div>

    <div class="form-group">
      <label class="form-label" for="clause-rec-textarea">Legal Recommendation</label>
      <textarea id="clause-rec-textarea" class="form-textarea" rows="2" placeholder="Counsel advice or suggested redline revision..."></textarea>
    </div>
  `;

  modal.open({
    title: `Flag Clause: ${contract.title}`,
    bodyContent: form,
    size: 'md',
    footerButtons: [
      {
        text: 'Cancel',
        variant: 'secondary',
        onClick: ({ close }) => close()
      },
      {
        text: 'Save Flagged Clause',
        variant: 'primary',
        onClick: async ({ close }) => {
          const type = form.querySelector('#clause-type-select').value;
          const risk = form.querySelector('#clause-risk-select').value;
          const page = form.querySelector('#clause-page-input').value.trim() || 'Page 1';
          const quote = form.querySelector('#clause-quote-textarea').value.trim();
          const rec = form.querySelector('#clause-rec-textarea').value.trim();

          if (!quote || !rec) {
            toast.warning('Please provide both the verbatim quote and legal recommendation.');
            return;
          }

          const newClause = {
            clause_type: type,
            risk_level: risk,
            page,
            quote,
            recommendation: rec
          };

          try {
            await legalApi.flagClause(contract.id, newClause);
            contract.flagged_clauses = contract.flagged_clauses || [];
            contract.flagged_clauses.unshift(newClause);
            toast.success(`Flagged clause "${type}" added to contract review.`);
            close();

            const listContainer = drawerContent.querySelector('#contract-clauses-list');
            if (listContainer) {
              listContainer.innerHTML = _renderClausesCards(contract.flagged_clauses);
            }
          } catch (e) {
            toast.error('Failed to save flagged clause to backend.');
          }
        }
      }
    ]
  });
}
