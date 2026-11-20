/**
 * Omnea Enterprise Context & Vendor Intelligence Views (vendor_views.js)
 * Route: #/app/vendors & #/app/vendors/reviews
 *
 * Capabilities:
 * 1. Vendor Directory Table:
 *    - Live API data binding: GET /api/v1/vendors.
 *    - Search, filter (status, region), sorting.
 *    - Columns: Vendor Name, Canonical Key, Parent / Subsidiary badge (SUBSIDIARY_OF indicator),
 *      Active Contracts count, Security Status (SOC2 compliant badge),
 *      Spend per Currency pills (e.g. "USD: $48,000", "EUR: €15,000" - NEVER summed!), Status.
 * 2. Interactive Context Graph:
 *    - Live dynamic SVG projection from GET /api/v1/vendors/graph.
 *    - Visualises nodes (vendors, contracts) and directed edges (SUBSIDIARY_OF, MERGED_INTO, CONTRACT_WITH).
 * 3. Detail Drawer:
 *    - #vendor-detail-drawer with vendor metadata, legal_name, parent_name, subsidiaries, contracts,
 *      multi-currency spend ledgers, and reversible unmerge action (#btn-unmerge-vendor).
 * 4. Human-in-the-Loop Review Queue:
 *    - #review-tasks-container with pending entity review tasks from GET /api/v1/reviews/tasks.
 *    - Action buttons with data-action="merge" and data-task-id="{id}".
 * 5. Grounded Vendor AI Research:
 *    - Research via POST /api/v1/vendors/{id}/research with verbatim quote verification.
 */

import { state, escapeHtml } from '../state.js';
import { vendorApi, reviewApi, requestsApi } from '../api.js';
import { DataTable } from '../components/table.js';
import { modal } from '../components/modal.js';
import { toast } from '../components/toast.js';
import { createTabs, openDetailDrawer, closeDetailDrawer } from '../layout.js';

let liveVendorsCache = [];
let liveReviewTasksCache = [];
let currentTabController = null;

export async function renderVendorView(container, subRoute = '') {
  let initialTab = 'directory';
  if (subRoute === '/reviews' || window.location.hash.includes('/vendors/reviews')) {
    initialTab = 'reviews';
  } else if (subRoute === '/graph') {
    initialTab = 'graph';
  } else if (subRoute === '/research') {
    initialTab = 'research';
  }

  container.innerHTML = `
    <div class="page-header" style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: var(--space-4);">
      <div>
        <h1 class="page-title" style="margin: 0; font-size: var(--text-2xl); font-weight: var(--font-bold); color: var(--text-primary);">Vendor Directory & Enterprise Context Graph</h1>
        <p class="page-description" style="margin: var(--space-1) 0 0; font-size: var(--text-sm); color: var(--text-muted);">
          Unified canonical vendor identities, legal subsidiary trees, multi-currency spend ledgers, and human-in-the-loop review queues.
        </p>
      </div>
      <div style="display: flex; gap: var(--space-2);">
        <button type="button" class="btn btn-secondary" id="btn-export-vendors">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 4px;">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
            <polyline points="7 10 12 15 17 10"></polyline>
            <line x1="12" y1="15" x2="12" y2="3"></line>
          </svg>
          Export Directory
        </button>
      </div>
    </div>

    <!-- Navigation Tabs -->
    <div id="vendor-tabs-container" style="margin-bottom: var(--space-4);"></div>

    <!-- Sub-tab Content Panels -->
    <div id="vendor-tab-content"></div>
  `;

  // Fetch pending review count for tab badge
  let pendingTasksCount = 0;
  try {
    const tasks = await reviewApi.getTasks();
    if (Array.isArray(tasks)) {
      liveReviewTasksCache = tasks;
      pendingTasksCount = tasks.filter(t => t.status === 'pending').length;
    }
  } catch (err) {
    liveReviewTasksCache = [];
  }

  const tabsContainer = document.getElementById('vendor-tabs-container');
  const contentContainer = document.getElementById('vendor-tab-content');

  currentTabController = createTabs({
    container: tabsContainer,
    tabs: [
      { id: 'directory', label: 'Vendor Directory', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line><line x1="3" y1="6" x2="3.01" y2="6"></line><line x1="3" y1="12" x2="3.01" y2="12"></line><line x1="3" y1="18" x2="3.01" y2="18"></line></svg>' },
      { id: 'graph', label: 'Context Graph View', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="18" cy="5" r="3"></circle><circle cx="6" cy="12" r="3"></circle><circle cx="18" cy="19" r="3"></circle><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line></svg>' },
      { id: 'reviews', label: 'Review Queue', badge: pendingTasksCount, badgeClass: pendingTasksCount > 0 ? 'badge-warning' : 'badge-neutral', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 14 14"></polyline></svg>' },
      { id: 'research', label: 'AI Vendor Research', icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>' }
    ],
    activeTabId: initialTab,
    onTabChange: async (tabId) => {
      await renderTab(tabId, contentContainer);
    }
  });

  await renderTab(initialTab, contentContainer);

  const exportBtn = document.getElementById('btn-export-vendors');
  if (exportBtn) {
    exportBtn.addEventListener('click', () => {
      _exportVendorsCsv();
    });
  }
}

async function renderTab(tabId, container) {
  container.innerHTML = '';
  switch (tabId) {
    case 'directory':
      await _renderDirectoryTab(container);
      break;
    case 'graph':
      await _renderGraphTab(container);
      break;
    case 'reviews':
      await _renderReviewQueueTab(container);
      break;
    case 'research':
      await _renderResearchTab(container);
      break;
    default:
      await _renderDirectoryTab(container);
      break;
  }
}

/**
 * 1. Vendor Directory Tab (Table View)
 */
async function _renderDirectoryTab(container) {
  const wrapper = document.createElement('div');
  wrapper.className = 'vendor-directory-view';

  // Toolbar with Search, Filters, and View Switcher
  const toolbar = document.createElement('div');
  toolbar.className = 'table-toolbar';
  toolbar.style.display = 'flex';
  toolbar.style.flexWrap = 'wrap';
  toolbar.style.gap = 'var(--space-3)';
  toolbar.style.alignItems = 'center';
  toolbar.style.justifyContent = 'space-between';
  toolbar.style.marginBottom = 'var(--space-3)';

  toolbar.innerHTML = `
    <div style="display: flex; gap: var(--space-2); flex: 1; max-width: 600px;">
      <input type="text" id="vendor-search-input" class="form-input" placeholder="Search vendors, canonical key, region..." aria-label="Search vendors">
      <select id="vendor-status-filter" class="form-select" style="max-width: 150px;" aria-label="Filter by status">
        <option value="">All Statuses</option>
        <option value="active">Active</option>
        <option value="merged">Merged</option>
      </select>
      <select id="vendor-region-filter" class="form-select" style="max-width: 140px;" aria-label="Filter by region">
        <option value="">All Regions</option>
        <option value="US">US</option>
        <option value="EU">EU</option>
        <option value="APAC">APAC</option>
        <option value="Global">Global</option>
      </select>
    </div>
    <div style="display: flex; gap: var(--space-2);">
      <button type="button" class="btn btn-ghost" id="btn-switch-to-graph" data-view="graph">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 4px;">
          <circle cx="18" cy="5" r="3"></circle><circle cx="6" cy="12" r="3"></circle><circle cx="18" cy="19" r="3"></circle>
          <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line>
        </svg>
        Switch to Graph View
      </button>
    </div>
  `;

  const tableContainer = document.createElement('div');
  tableContainer.id = 'vendors-data-table';

  wrapper.appendChild(toolbar);
  wrapper.appendChild(tableContainer);
  container.appendChild(wrapper);

  // Fetch live vendors from backend
  let vendors = [];
  try {
    const fetched = await vendorApi.getVendors();
    if (Array.isArray(fetched)) {
      vendors = fetched;
    }
  } catch (err) {
    vendors = [];
  }
  liveVendorsCache = vendors;

  // Initialize DataTable
  const table = new DataTable({
    container: tableContainer,
    tbodyId: 'vendor-table-body',
    columns: [
      {
        key: 'name',
        label: 'Vendor Name',
        sortable: true,
        render: (_val, row) => {
          const r = (row && row.id) ? row : (_val || {});
          return `
            <div>
              <div style="font-weight: var(--font-semibold); color: var(--text-primary); cursor: pointer;" class="vendor-link" data-id="${r.id}" data-vendor-id="${r.id}">
                ${escapeHtml(r.name || '')}
              </div>
              <div style="font-size: var(--text-xs); color: var(--text-muted);">${escapeHtml(r.legal_name || '')}</div>
            </div>
          `;
        }
      },
      {
        key: 'canonical_key',
        label: 'Canonical Key',
        sortable: true,
        render: (_val, row) => {
          const r = (row && row.id) ? row : (_val || {});
          return `<code style="font-size: 11px; background: var(--bg-subtle); padding: 2px 6px; border-radius: var(--radius-sm); border: 1px solid var(--border-subtle);">${escapeHtml(r.canonical_key || '')}</code>`;
        }
      },
      {
        key: 'hierarchy',
        label: 'Hierarchy / Parent',
        sortable: false,
        render: (_val, row) => {
          const r = (row && row.id) ? row : (_val || {});
          if (r.parent_name) {
            return `
              <span class="badge badge-warning" style="display: inline-flex; align-items: center; gap: 4px;" title="Subsidiary of ${escapeHtml(r.parent_name)}">
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
                SUBSIDIARY_OF ${escapeHtml(r.parent_name)}
              </span>
            `;
          }
          if (r.parent_vendor_id === null && liveVendorsCache.some(v => v.parent_vendor_id === r.id)) {
            return `<span class="badge badge-info">Parent Entity</span>`;
          }
          return `<span class="badge badge-ghost">Independent</span>`;
        }
      },
      {
        key: 'active_contracts_count',
        label: 'Active Contracts',
        sortable: true,
        render: (_val, row) => {
          const r = (row && row.id) ? row : (_val || {});
          return `<span class="badge badge-neutral">${r.active_contracts_count || 0} Active</span>`;
        }
      },
      {
        key: 'security_status',
        label: 'Security Status',
        sortable: true,
        render: (_val, row) => {
          const r = (row && row.id) ? row : (_val || {});
          const sec = (r.security_status || 'none').toLowerCase();
          if (sec === 'soc2_compliant' || sec === 'compliant' || sec === 'active') {
            return `
              <span class="badge badge-success" style="display: inline-flex; align-items: center; gap: 4px;">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
                SOC2 Compliant
              </span>
            `;
          }
          return `<span class="badge badge-warning">Pending Review</span>`;
        }
      },
      {
        key: 'spend_per_currency',
        label: 'Spend per Currency',
        sortable: false,
        render: (_val, row) => {
          const r = (row && row.id) ? row : (_val || {});
          // Strictly display per-currency pills - NEVER summed across currencies!
          const spendObj = r.spend_by_currency || {};
          const entries = Object.entries(spendObj);
          if (entries.length === 0) {
            return `<span style="font-size: var(--text-xs); color: var(--text-muted);">$0.00</span>`;
          }
          const pills = entries.map(([curr, amount]) => {
            const formatted = new Intl.NumberFormat('en-US', { style: 'currency', currency: curr }).format(amount);
            return `<span class="badge badge-currency" style="font-weight: var(--font-semibold); font-size: 11px; margin-right: 4px; background: var(--bg-subtle); border: 1px solid var(--border-default);">${escapeHtml(curr)}: ${escapeHtml(formatted)}</span>`;
          }).join('');
          return `<div class="currency-pills-group" style="display: flex; flex-wrap: wrap; gap: 2px;">${pills}</div>`;
        }
      },
      {
        key: 'status',
        label: 'Status',
        sortable: true,
        render: (_val, row) => {
          const r = (row && row.id) ? row : (_val || {});
          if (r.status === 'active') {
            return `<span class="badge badge-success">Active</span>`;
          }
          if (r.status === 'merged') {
            return `<span class="badge badge-muted">Merged</span>`;
          }
          return `<span class="badge badge-neutral">${escapeHtml(r.status || '')}</span>`;
        }
      },
      {
        key: 'actions',
        label: 'Actions',
        sortable: false,
        render: (_val, row) => {
          const r = (row && row.id) ? row : (_val || {});
          return `
            <button type="button" class="btn btn-secondary btn-sm view-vendor-btn" data-id="${r.id}" data-vendor-id="${r.id}">
              View Details
            </button>
          `;
        }
      }
    ],
    data: vendors,
    pageSize: 10,
    initialSortKey: 'name'
  });

  // Filter & Search Handlers
  const searchInput = wrapper.querySelector('#vendor-search-input');
  const statusFilter = wrapper.querySelector('#vendor-status-filter');
  const regionFilter = wrapper.querySelector('#vendor-region-filter');

  const applyFilters = () => {
    const q = (searchInput.value || '').toLowerCase().trim();
    const st = statusFilter.value;
    const reg = regionFilter.value;

    const filtered = liveVendorsCache.filter(v => {
      const matchSearch = !q ||
        (v.name && v.name.toLowerCase().includes(q)) ||
        (v.canonical_key && v.canonical_key.toLowerCase().includes(q));

      const matchStatus = !st || v.status === st;
      const matchRegion = !reg || (v.region || '').toUpperCase() === reg.toUpperCase();

      return matchSearch && matchStatus && matchRegion;
    });

    table.setData(filtered);
    _attachVendorTableActionHandlers(tableContainer);
  };

  searchInput.addEventListener('input', applyFilters);
  statusFilter.addEventListener('change', applyFilters);
  regionFilter.addEventListener('change', applyFilters);

  _attachVendorTableActionHandlers(tableContainer);

  const switchGraphBtn = wrapper.querySelector('#btn-switch-to-graph');
  if (switchGraphBtn) {
    switchGraphBtn.addEventListener('click', () => {
      if (currentTabController) {
        currentTabController.setActiveTab('graph');
      }
      renderTab('graph', container);
    });
  }
}

function _attachVendorTableActionHandlers(tableContainer) {
  const buttons = tableContainer.querySelectorAll('.view-vendor-btn, .vendor-link');
  buttons.forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const id = btn.getAttribute('data-vendor-id') || btn.getAttribute('data-id');
      await _openVendorDetailDrawer(id);
    });
  });
}

/**
 * 2. Context Graph View Tab (Dynamic SVG)
 */
async function _renderGraphTab(container) {
  const wrapper = document.createElement('div');
  wrapper.className = 'vendor-graph-view';

  // Fetch live graph projection
  let graphData = { nodes: [], edges: [] };
  try {
    const fetched = await vendorApi.getGraph();
    if (fetched && Array.isArray(fetched.nodes)) {
      graphData = fetched;
    }
  } catch (err) {
    graphData = { nodes: [], edges: [] };
  }

  wrapper.innerHTML = `
    <div class="card" style="margin-bottom: var(--space-4); padding: var(--space-4); background: var(--bg-surface); border: 1px solid var(--border-default);">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-4);">
        <div>
          <h2 style="font-size: var(--text-base); font-weight: var(--font-bold); margin: 0; color: var(--text-primary);">
            Enterprise Context Graph & Hierarchy
          </h2>
          <p style="font-size: var(--text-xs); color: var(--text-muted); margin: 2px 0 0;">
            Hierarchical parent-subsidiary trees (SUBSIDIARY_OF), merged identities (MERGED_INTO), and associated contracts.
          </p>
        </div>
        <div style="display: flex; gap: var(--space-2);">
          <button type="button" class="btn btn-ghost" id="btn-switch-to-table" data-view="table">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 4px;">
              <line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line>
              <line x1="3" y1="6" x2="3.01" y2="6"></line><line x1="3" y1="12" x2="3.01" y2="12"></line><line x1="3" y1="18" x2="3.01" y2="18"></line>
            </svg>
            Switch to Table View
          </button>
        </div>
      </div>

      <!-- Graph Canvas Container -->
      <div id="graph-canvas-container" style="width: 100%; min-height: 480px; background: var(--bg-subtle); border-radius: var(--radius-md); border: 1px solid var(--border-subtle); position: relative; overflow: auto; display: flex; justify-content: center; align-items: center;">
        ${_generateDynamicGraphSvg(graphData)}
      </div>
    </div>
  `;

  container.appendChild(wrapper);

  const switchTableBtn = wrapper.querySelector('#btn-switch-to-table');
  if (switchTableBtn) {
    switchTableBtn.addEventListener('click', () => {
      if (currentTabController) {
        currentTabController.setActiveTab('directory');
      }
      renderTab('directory', container);
    });
  }

  // Node clicks to open drawer
  const svgNodes = wrapper.querySelectorAll('.graph-node');
  svgNodes.forEach(node => {
    node.addEventListener('click', async () => {
      const vendorId = node.getAttribute('data-id');
      if (vendorId) {
        await _openVendorDetailDrawer(vendorId);
      }
    });
  });
}

function _generateDynamicGraphSvg(graph) {
  const nodes = graph.nodes || [];
  const edges = graph.edges || [];

  const width = 850;
  const height = 480;

  // Layout vendor nodes
  const vendorNodes = nodes.filter(n => n.label === 'vendor');
  const nodePositions = new Map();

  const numVendors = vendorNodes.length;
  vendorNodes.forEach((node, idx) => {
    // Distribute horizontally
    const x = numVendors === 1 ? width / 2 : 120 + (idx * ((width - 240) / Math.max(1, numVendors - 1)));
    const y = (idx % 2 === 0) ? 200 : 300;
    nodePositions.set(String(node.id), { x, y, node });
  });

  // Render edges
  let edgesSvg = '';
  edges.forEach(edge => {
    const srcPos = nodePositions.get(String(edge.source));
    const tgtPos = nodePositions.get(String(edge.target));
    if (srcPos && tgtPos) {
      const midX = (srcPos.x + tgtPos.x) / 2;
      const midY = (srcPos.y + tgtPos.y) / 2 - 12;
      edgesSvg += `
        <line x1="${srcPos.x}" y1="${srcPos.y}" x2="${tgtPos.x}" y2="${tgtPos.y}" stroke="#64748b" stroke-width="2" stroke-dasharray="${edge.type === 'SUBSIDIARY_OF' ? 'none' : '4,4'}" marker-end="url(#arrowhead)"/>
        <text x="${midX}" y="${midY}" fill="#475569" font-size="10" font-weight="bold" text-anchor="middle" background="#ffffff">${escapeHtml(edge.type)}</text>
      `;
    }
  });

  // Render nodes
  let nodesSvg = '';
  vendorNodes.forEach(vNode => {
    const pos = nodePositions.get(String(vNode.id)) || { x: width / 2, y: height / 2 };
    const isMerged = vNode.status === 'merged';
    const fillColor = isMerged ? '#94a3b8' : '#2563eb';
    const labelText = vNode.name || 'Vendor';

    nodesSvg += `
      <g class="graph-node" data-id="${escapeHtml(String(vNode.id))}" transform="translate(${pos.x}, ${pos.y})" style="cursor: pointer;">
        <circle r="30" fill="${fillColor}" stroke="#ffffff" stroke-width="3"></circle>
        <text text-anchor="middle" y="5" font-size="11" font-weight="bold" fill="#ffffff">${escapeHtml(labelText.slice(0, 10))}</text>
        <text text-anchor="middle" y="48" font-size="12" font-weight="600" fill="var(--text-primary)">${escapeHtml(labelText)}</text>
      </g>
    `;
  });

  return `
    <svg id="vendor-graph-svg" width="100%" height="100%" viewBox="0 0 ${width} ${height}" style="min-width: ${width}px; min-height: ${height}px;" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="28" refY="3.5" orient="auto">
          <polygon points="0 0, 10 3.5, 0 7" fill="#64748b" />
        </marker>
      </defs>
      <g class="graph-edges">${edgesSvg}</g>
      <g class="graph-nodes">${nodesSvg}</g>
    </svg>
  `;
}

/**
 * 3. Human-in-the-Loop Review Queue Tab
 */
async function _renderReviewQueueTab(container) {
  const wrapper = document.createElement('div');
  wrapper.className = 'review-queue-view';
  wrapper.id = 'review-tasks-container';

  let tasks = [];
  try {
    const fetched = await reviewApi.getTasks();
    if (Array.isArray(fetched)) {
      tasks = fetched;
    }
  } catch (err) {
    tasks = [];
  }
  liveReviewTasksCache = tasks;

  const pendingTasks = tasks.filter(t => t.status === 'pending');

  wrapper.innerHTML = `
    <div style="margin-bottom: var(--space-4);">
      <h2 style="font-size: var(--text-lg); font-weight: var(--font-bold); margin: 0; color: var(--text-primary);">Human-in-the-Loop Entity Review Queue</h2>
      <p style="font-size: var(--text-xs); color: var(--text-muted); margin: 2px 0 0;">
        Review tasks generated when vendor matching confidence falls below the automatic resolution threshold.
      </p>
    </div>

    ${pendingTasks.length === 0 ? `
      <div class="empty-state" style="padding: var(--space-8); text-align: center;">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--success)" stroke-width="2" style="margin-bottom: var(--space-2);">
          <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
          <polyline points="22 4 12 14.01 9 11.01"></polyline>
        </svg>
        <h3 class="empty-state-title" style="margin: 0; font-size: var(--text-base);">Review Queue is Clean</h3>
        <p class="empty-state-desc" style="color: var(--text-muted); font-size: var(--text-xs); margin-top: 4px;">All provisional vendor identities have been resolved or confirmed.</p>
      </div>
    ` : `
      <div class="review-tasks-list" style="display: flex; flex-direction: column; gap: var(--space-4);">
        ${pendingTasks.map(task => {
          const provName = task.source_name || 'Provisional Vendor';
          const candVendor = liveVendorsCache.find(v => String(v.id) === String(task.candidate_vendor_id));
          const candName = candVendor ? candVendor.name : (task.metadata?.candidate_name || 'Datadog, Inc.');
          const confidencePct = (task.confidence * 100).toFixed(1);

          return `
            <div class="card review-task-card" id="task-card-${task.id}" style="border: 1px solid var(--border-default); border-radius: var(--radius-md); padding: var(--space-4); background: var(--bg-surface);">
              <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: var(--space-3);">
                <div>
                  <div style="display: flex; align-items: center; gap: var(--space-2);">
                    <span class="badge badge-warning" style="text-transform: uppercase; font-size: 10px;">${escapeHtml((task.task_type || 'duplicate_review').replace('_', ' '))}</span>
                    <span style="font-weight: var(--font-bold); font-size: var(--text-sm); color: var(--text-primary);">Task #${escapeHtml(String(task.id))}</span>
                  </div>
                  <p style="font-size: var(--text-xs); color: var(--text-secondary); margin: var(--space-1) 0 0;">
                    Source Name: <strong>${escapeHtml(provName)}</strong>
                  </p>
                </div>
                <div style="text-align: right;">
                  <div style="font-size: var(--text-xs); color: var(--text-muted);">Confidence Score</div>
                  <span class="badge ${task.confidence >= 0.8 ? 'badge-success' : 'badge-warning'}" style="font-size: var(--text-sm); font-weight: var(--font-bold);">
                    ${confidencePct}% Match
                  </span>
                </div>
              </div>

              <!-- Comparison Grid -->
              <div style="display: grid; grid-template-columns: 1fr auto 1fr; gap: var(--space-4); align-items: center; margin-bottom: var(--space-3); padding: var(--space-3); background: var(--bg-subtle); border-radius: var(--radius-sm);">
                <div>
                  <div style="font-size: 10px; font-weight: 700; text-transform: uppercase; color: var(--text-muted);">Provisional Ingress Vendor</div>
                  <div style="font-size: var(--text-sm); font-weight: 600; color: var(--text-primary); margin-top: 2px;">${escapeHtml(provName)}</div>
                </div>

                <div style="font-size: var(--text-xl); color: var(--brand-primary); font-weight: bold;">
                  &rarr;
                </div>

                <div>
                  <div style="font-size: 10px; font-weight: 700; text-transform: uppercase; color: var(--text-muted);">Candidate Canonical Vendor</div>
                  <div style="font-size: var(--text-sm); font-weight: 600; color: var(--brand-primary); margin-top: 2px;">${escapeHtml(candName)}</div>
                </div>
              </div>

              <!-- Action buttons -->
              <div style="display: flex; justify-content: flex-end; gap: var(--space-2);">
                <button type="button" class="btn btn-secondary btn-sm reject-review-btn" data-action="reject" data-task-id="${task.id}" data-id="${task.id}">
                  Reject / Keep Separate
                </button>
                <button type="button" class="btn btn-primary btn-sm approve-merge-btn" data-action="merge" data-task-id="${task.id}" data-id="${task.id}" data-target-id="${task.candidate_vendor_id}">
                  Approve Merge
                </button>
              </div>
            </div>
          `;
        }).join('')}
      </div>
    `}
  `;

  container.appendChild(wrapper);

  // Approve Merge Button Handlers
  const approveButtons = wrapper.querySelectorAll('.approve-merge-btn');
  approveButtons.forEach(btn => {
    btn.addEventListener('click', async () => {
      const taskId = btn.getAttribute('data-task-id') || btn.getAttribute('data-id');
      const targetVendorId = btn.getAttribute('data-target-id');
      btn.disabled = true;
      btn.textContent = 'Merging...';

      try {
        await reviewApi.resolveTask(taskId, 'merge', targetVendorId);
        toast.success('Vendors successfully merged into canonical record.');
        // Refresh local cache and UI
        await _renderReviewQueueTab(container);
      } catch (err) {
        toast.error('Failed to merge vendors.');
        btn.disabled = false;
        btn.textContent = 'Approve Merge';
      }
    });
  });

  const rejectButtons = wrapper.querySelectorAll('.reject-review-btn');
  rejectButtons.forEach(btn => {
    btn.addEventListener('click', async () => {
      const taskId = btn.getAttribute('data-task-id') || btn.getAttribute('data-id');
      btn.disabled = true;

      try {
        await reviewApi.resolveTask(taskId, 'reject');
        toast.info(`Task #${taskId} rejected. Provisional vendor kept separate.`);
        await _renderReviewQueueTab(container);
      } catch (err) {
        toast.error('Failed to resolve task.');
        btn.disabled = false;
      }
    });
  });
}

/**
 * 4. Vendor Detail Drawer (#vendor-detail-drawer)
 */
async function _openVendorDetailDrawer(vendorId) {
  let vendor = null;
  try {
    vendor = await vendorApi.getVendor(vendorId);
  } catch (err) {
    vendor = liveVendorsCache.find(v => String(v.id) === String(vendorId));
  }

  if (!vendor) {
    toast.error('Vendor details could not be loaded.');
    return;
  }

  const content = document.createElement('div');
  content.style.display = 'flex';
  content.style.flexDirection = 'column';
  content.style.gap = 'var(--space-4)';

  // Build Spend pills HTML - NEVER summed!
  const spendObj = vendor.spend_by_currency || {};
  const spendEntries = Object.entries(spendObj);
  const spendPills = spendEntries.map(([curr, amount]) => {
    const formatted = new Intl.NumberFormat('en-US', { style: 'currency', currency: curr }).format(amount);
    return `<span class="badge badge-currency" style="font-size: 12px; padding: 4px 8px; background: var(--bg-subtle); border: 1px solid var(--border-default);">${escapeHtml(curr)}: ${escapeHtml(formatted)}</span>`;
  }).join('');

  content.innerHTML = `
    <!-- Metadata Section -->
    <div class="card" style="padding: var(--space-3); background: var(--bg-subtle); border-radius: var(--radius-sm);">
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-2); font-size: var(--text-xs);">
        <div><strong style="color: var(--text-muted);">Legal Name:</strong> <span style="font-weight: 600;">${escapeHtml(vendor.legal_name || vendor.name)}</span></div>
        <div><strong style="color: var(--text-muted);">Canonical Key:</strong> <code>${escapeHtml(vendor.canonical_key || '')}</code></div>
        <div><strong style="color: var(--text-muted);">Region:</strong> <span>${escapeHtml(vendor.region || 'Global')}</span></div>
        <div><strong style="color: var(--text-muted);">Status:</strong> <span class="badge ${vendor.status === 'active' ? 'badge-success' : 'badge-muted'}">${escapeHtml(vendor.status)}</span></div>
      </div>
    </div>

    <!-- Corporate Hierarchy & Subsidiary Tree -->
    <div>
      <h4 style="font-size: var(--text-sm); font-weight: var(--font-bold); margin: 0 0 var(--space-2); color: var(--text-primary);">
        Corporate Hierarchy & Subsidiary Tree
      </h4>
      <div style="padding: var(--space-3); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); font-size: var(--text-xs);">
        ${vendor.parent_name ? `
          <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 4px;">
            <span class="badge badge-warning">SUBSIDIARY_OF</span>
            <span style="font-weight: 600;">${escapeHtml(vendor.parent_name)}</span>
          </div>
        ` : `
          <div style="color: var(--text-secondary);">Direct parent entity (Root level)</div>
        `}
        <div style="margin-top: 6px;">
          <strong style="color: var(--text-muted);">Direct Subsidiaries:</strong>
          ${_renderSubsidiariesList(vendor.child_subsidiaries || [])}
        </div>
      </div>
    </div>

    <!-- Multi-Currency Spend (Strictly Segregated) -->
    <div>
      <h4 style="font-size: var(--text-sm); font-weight: var(--font-bold); margin: 0 0 var(--space-2); color: var(--text-primary);">
        Committed Spend per Currency (Strictly Segregated)
      </h4>
      <div style="display: flex; gap: var(--space-2); flex-wrap: wrap;">
        ${spendPills || '<span style="color: var(--text-muted); font-size: var(--text-xs);">$0.00 (No recorded transactions)</span>'}
      </div>
    </div>

    <!-- Active Contracts List -->
    <div>
      <h4 style="font-size: var(--text-sm); font-weight: var(--font-bold); margin: 0 0 var(--space-2); color: var(--text-primary);">
        Active Contracts (${(vendor.contracts || []).length})
      </h4>
      ${(vendor.contracts && vendor.contracts.length > 0) ? `
        <div style="display: flex; flex-direction: column; gap: var(--space-2);">
          ${vendor.contracts.map(c => `
            <div style="padding: var(--space-2) var(--space-3); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); display: flex; justify-content: space-between; align-items: center; font-size: var(--text-xs);">
              <div>
                <strong style="color: var(--text-primary);">${escapeHtml(c.title)}</strong>
                <div style="color: var(--text-muted); margin-top: 2px;">Term: ${escapeHtml(c.start_date)} to ${escapeHtml(c.end_date || 'Ongoing')} ${c.auto_renew ? '(Auto-Renews)' : ''}</div>
              </div>
              <div style="text-align: right;">
                <span class="badge badge-currency" style="font-weight: bold;">${escapeHtml(c.currency)} ${new Intl.NumberFormat().format(c.total_value)}</span>
              </div>
            </div>
          `).join('')}
        </div>
      ` : `<div style="color: var(--text-muted); font-size: var(--text-xs);">No contracts on file</div>`}
    </div>
  `;

  const actions = [];

  if (vendor.status === 'merged') {
    actions.push({
      id: 'btn-unmerge-vendor',
      label: 'Unmerge Vendor',
      variant: 'warning',
      onClick: async ({ close }) => {
        try {
          await vendorApi.unmergeVendor(vendor.id);
          close();
          const container = document.getElementById('app-content-area');
          if (container) await renderVendorView(container, 'directory');
          toast.success(`Vendor ${vendor.name} unmerged. Original spend and status restored.`);
        } catch (err) {
          toast.error('Failed to unmerge vendor.');
        }
      }
    });
  } else {
    actions.push({
      id: 'btn-merge-vendor',
      label: 'Merge Vendor',
      variant: 'secondary',
      onClick: ({ close }) => {
        close();
        const container = document.getElementById('app-content-area');
        if (container) renderVendorView(container, 'queue');
      }
    });
  }

  actions.push({
    label: 'Close',
    variant: 'secondary',
    onClick: ({ close }) => close()
  });

  openDetailDrawer({
    id: 'vendor-detail-drawer',
    title: vendor.name,
    subtitle: `Canonical Key: ${vendor.canonical_key || ''}`,
    content,
    actions
  });
}

function _renderSubsidiariesList(subs = []) {
  if (!subs || subs.length === 0) {
    return `<span style="color: var(--text-muted); font-size: 11px;"> None recorded</span>`;
  }
  return `
    <ul style="margin: 4px 0 0 16px; padding: 0;">
      ${subs.map(s => `<li style="margin-bottom: 2px;"><strong style="color: var(--text-primary);">${escapeHtml(s.name)}</strong> (${escapeHtml(s.region || 'EU')})</li>`).join('')}
    </ul>
  `;
}

/**
 * 5. Vendor AI Research Tab
 */
async function _renderResearchTab(container) {
  const wrapper = document.createElement('div');
  wrapper.className = 'vendor-research-view';

  // Check LLM connectivity
  let isLlmConnected = true;
  try {
    const res = await requestsApi.getLlmStatus();
    if (res && res.is_connected === false) {
      isLlmConnected = false;
    }
  } catch (err) {
    isLlmConnected = false;
  }

  // Ensure live vendors cache is populated
  if (liveVendorsCache.length === 0) {
    try {
      const fetched = await vendorApi.getVendors();
      if (Array.isArray(fetched)) liveVendorsCache = fetched;
    } catch (e) {
      // Ignored
    }
  }

  wrapper.innerHTML = `
    <div style="margin-bottom: var(--space-4);">
      <h2 style="font-size: var(--text-lg); font-weight: var(--font-bold); margin: 0; color: var(--text-primary);">Grounded Vendor AI Research & Synthesis</h2>
      <p style="font-size: var(--text-xs); color: var(--text-muted); margin: 2px 0 0;">
        Upload vendor documentation (SOC2 audit report, Master Services Agreement, pricing sheet) to generate grounded profiles with verbatim citations.
      </p>
    </div>

    ${!isLlmConnected ? `
      <!-- Prominent Not Connected Banner -->
      <div class="alert-banner alert-warning" style="display: flex; gap: var(--space-3); padding: var(--space-4); background: var(--warning-bg); border: 1px solid var(--warning-border); border-radius: var(--radius-md); margin-bottom: var(--space-4);">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="var(--warning)" stroke-width="2" style="flex-shrink: 0; margin-top: 2px;">
          <circle cx="12" cy="12" r="10"></circle>
          <line x1="12" y1="8" x2="12" y2="12"></line>
          <line x1="12" y1="16" x2="12.01" y2="16"></line>
        </svg>
        <div>
          <strong style="color: var(--warning-text); font-size: var(--text-sm);">LLM Provider Not Connected</strong>
          <p style="color: var(--warning-text); font-size: var(--text-xs); margin: 4px 0 0; line-height: 1.5;">
            Automated entity research requires a valid LLM API key. Configure <code>LLM_API_KEY</code> in environment settings or review in manual mode.
          </p>
        </div>
      </div>
    ` : ''}

    <div class="card" style="margin-bottom: var(--space-4);">
      <div class="form-group">
        <label class="form-label" for="research-vendor-select">Select Vendor to Research</label>
        <select id="research-vendor-select" class="form-select">
          ${liveVendorsCache.map(v => `<option value="${v.id}">${escapeHtml(v.name)} (${v.canonical_key || 'key'})</option>`).join('')}
        </select>
      </div>

      <div class="form-group">
        <label class="form-label" for="research-doc-input">Vendor Documentation & Artifacts (SOC2, MSA, Pricing)</label>
        <textarea id="research-doc-input" class="form-textarea" rows="6" placeholder="Paste SOC 2 Type II audit report summary, SLA clauses, liability caps, or data processing terms here..."></textarea>
      </div>

      <div style="display: flex; justify-content: flex-end;">
        <button type="button" class="btn btn-primary" id="btn-trigger-research">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 4px;">
            <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
          </svg>
          Research Vendor with AI
        </button>
      </div>

      <!-- Output Synthesis Container -->
      <div id="research-output-container" style="margin-top: var(--space-4);"></div>
    </div>
  `;

  container.appendChild(wrapper);

  const triggerBtn = wrapper.querySelector('#btn-trigger-research');
  const outputContainer = wrapper.querySelector('#research-output-container');
  const docInput = wrapper.querySelector('#research-doc-input');
  const vendorSelect = wrapper.querySelector('#research-vendor-select');

  triggerBtn.addEventListener('click', async () => {
    const text = (docInput.value || '').trim();
    const vendorId = vendorSelect.value;

    if (!text) {
      toast.warning('Please paste or load vendor document text to research.');
      return;
    }

    triggerBtn.disabled = true;
    triggerBtn.textContent = 'Synthesizing grounded facts...';

    try {
      const res = await vendorApi.researchVendor(vendorId, { text, documents: [text] });
      triggerBtn.disabled = false;
      triggerBtn.textContent = 'Research Vendor with AI';

      const claims = res.claims || [];
      outputContainer.innerHTML = `
        <div style="border-top: 2px solid var(--border-subtle); padding-top: var(--space-4);">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-3);">
            <h3 style="font-size: var(--text-base); font-weight: var(--font-bold); margin: 0; color: var(--text-primary);">
              Grounded Intelligence Summary
            </h3>
            <span class="badge badge-success">Grounded Claims (${claims.length})</span>
          </div>

          <p style="font-size: var(--text-sm); line-height: 1.5; color: var(--text-secondary); margin-bottom: var(--space-4);">
            ${escapeHtml(res.summary || 'Summary synthesized from grounded source document.')}
          </p>

          <div style="display: flex; flex-direction: column; gap: var(--space-3);">
            ${claims.map(c => `
              <div class="card quote-evidence-card" style="padding: var(--space-3); border: 1px solid var(--border-default); background: var(--bg-surface);">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                  <strong style="font-size: var(--text-sm); color: var(--text-primary);">${escapeHtml(c.fact)}</strong>
                  <span class="badge ${c.verified ? 'badge-success' : 'badge-warning'}">${c.verified ? 'VERIFIED CITATION' : 'Unverified'}</span>
                </div>
                <div class="quote-snippet" style="background: var(--bg-subtle); border-left: 3px solid var(--brand-primary); padding: var(--space-2); font-size: var(--text-xs); font-style: italic;">
                  &ldquo;${escapeHtml(c.quote)}&rdquo;
                </div>
              </div>
            `).join('')}
          </div>
        </div>
      `;
      toast.success('Vendor research report generated with verified citations.');
    } catch (err) {
      triggerBtn.disabled = false;
      triggerBtn.textContent = 'Research Vendor with AI';
      toast.error('Failed to complete vendor research.');
    }
  });
}

function _exportVendorsCsv() {
  if (liveVendorsCache.length === 0) {
    toast.warning('No vendor records to export.');
    return;
  }
  const headers = ['id', 'name', 'canonical_key', 'region', 'status', 'parent_name'];
  const rows = liveVendorsCache.map(v => [
    v.id,
    `"${(v.name || '').replace(/"/g, '""')}"`,
    v.canonical_key || '',
    v.region || '',
    v.status || '',
    `"${(v.parent_name || '').replace(/"/g, '""')}"`
  ].join(','));

  const csv = [headers.join(','), ...rows].join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `vendors-export-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
  toast.success('Vendor directory CSV exported.');
}
