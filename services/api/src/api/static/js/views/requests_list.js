/**
 * Omnea Procurement Requests List View (requests_list.js)
 * Route: #/app/requests
 * Displays "My Requests" table with columns:
 * ID, Vendor, Amount, Currency, Status Badge, Created At, Action (View Details).
 * Hooked up to SSE updates (state.subscribeToTenantStream) for live status refresh.
 */

import { state, escapeHtml } from '../state.js';
import { requestsApi } from '../api.js';
import { DataTable } from '../components/table.js';
import { toast } from '../components/toast.js';

let activeSseUnsubscribe = null;

export async function renderRequestsList(container) {
  // Clean up any previous SSE subscription
  if (typeof activeSseUnsubscribe === 'function') {
    activeSseUnsubscribe();
    activeSseUnsubscribe = null;
  }

  container.innerHTML = `
    <div class="page-header">
      <div>
        <h1 class="page-title">My Procurement Requests</h1>
        <p class="page-description">
          Monitor your active purchase requests, live multi-agent evaluations, and approval decisions.
        </p>
      </div>
      <div style="display: flex; gap: var(--space-3);">
        <button type="button" class="btn btn-secondary" id="refresh-requests-btn" title="Refresh Table">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="23 4 23 10 17 10"></polyline>
            <polyline points="1 20 1 14 7 14"></polyline>
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
          </svg>
          Refresh
        </button>
        <a href="#/app/requests/new" class="btn btn-primary" id="btn-new-request">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="12" y1="5" x2="12" y2="19"></line>
            <line x1="5" y1="12" x2="19" y2="12"></line>
          </svg>
          Submit New Request
        </a>
      </div>
    </div>

    <!-- Live Status & Offline Banners -->
    <div id="requests-banner-container">
      <div id="requests-sse-badge" style="display: flex; align-items: center; gap: var(--space-2); margin-bottom: var(--space-4); font-size: var(--text-xs); color: var(--text-muted);">
        <span class="badge-dot" style="background-color: var(--success); width: 8px; height: 8px;"></span>
        <span>Live updates connected via tenant stream</span>
      </div>
    </div>

    <!-- Table Container -->
    <div id="requests-table-wrapper">
      <div class="skeleton" style="height: 320px; border-radius: var(--radius-lg);"></div>
    </div>
  `;

  const tableWrapper = container.querySelector('#requests-table-wrapper');
  const refreshBtn = container.querySelector('#refresh-requests-btn');
  const bannerContainer = container.querySelector('#requests-banner-container');
  const newRequestBtn = container.querySelector('#btn-new-request');

  let dataTable = null;

  async function loadData(showToast = false) {
    let isOffline = false;
    let items = [];

    // Pre-flight check: is browser explicitly offline?
    const networkOffline = typeof navigator !== 'undefined' && navigator.onLine === false;
    if (networkOffline && window.procureDesktop?.getOfflineRequests) {
      try {
        const cached = await window.procureDesktop.getOfflineRequests();
        if (cached) {
          items = Array.isArray(cached) ? cached : (cached.items || []);
          isOffline = true;
        }
      } catch (cacheErr) {
        console.warn('Failed to get offline requests from desktop cache:', cacheErr);
      }
    }

    if (!isOffline) {
      try {
        let rawData = null;
        try {
          rawData = await requestsApi.getRequests();
        } catch (fetchErr) {
          // If fetch throws TypeError: Failed to fetch or network error / server unreachable
          const isNetworkError =
            !navigator.onLine ||
            fetchErr instanceof TypeError ||
            fetchErr?.name === 'TypeError' ||
            (fetchErr?.message && (
              fetchErr.message.includes('fetch') ||
              fetchErr.message.includes('Network Error') ||
              fetchErr.message.includes('Unable to reach server') ||
              fetchErr.message.includes('Failed to fetch')
            ));

          if (isNetworkError && window.procureDesktop?.getOfflineRequests) {
            try {
              const cached = await window.procureDesktop.getOfflineRequests();
              if (cached) {
                rawData = cached;
                isOffline = true;
              }
            } catch (cacheErr) {
              console.warn('Failed to retrieve offline requests from desktop:', cacheErr);
            }
          }

          if (!rawData) {
            // Fallback to dashboard requests endpoint if primary is still in flight
            try {
              const { api } = await import('../api.js');
              rawData = await api.get('/api/v1/dashboard/requests');
            } catch {
              if (window.procureDesktop?.getOfflineRequests) {
                const cached = await window.procureDesktop.getOfflineRequests();
                if (cached) {
                  rawData = cached;
                  isOffline = true;
                } else {
                  throw fetchErr;
                }
              } else {
                throw fetchErr;
              }
            }
          }
        }

        if (rawData && rawData.isOffline) {
          isOffline = true;
        }

        // Handle items whether array or paginated response
        items = Array.isArray(rawData)
          ? rawData
          : (rawData && Array.isArray(rawData.items))
          ? rawData.items
          : [];

        // Whenever /api/v1/requests successfully returns items, dispatch caching:
        if (!isOffline && window.procureDesktop?.saveOfflineRequests) {
          try {
            window.procureDesktop.saveOfflineRequests(items);
          } catch (e) {
            console.warn('Failed to save offline requests to desktop cache:', e);
          }
        }
      } catch (err) {
        // Final fallback to desktop offline cache
        if (window.procureDesktop?.getOfflineRequests) {
          try {
            const cached = await window.procureDesktop.getOfflineRequests();
            if (cached) {
              items = Array.isArray(cached) ? cached : (cached.items || []);
              isOffline = true;
            }
          } catch (cacheErr) {
            console.warn('Desktop getOfflineRequests error:', cacheErr);
          }
        }

        if (!isOffline) {
          console.error('Failed to load requests:', err);
          tableWrapper.innerHTML = `
            <div class="empty-state">
              <svg class="empty-state-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"></circle>
                <line x1="12" y1="8" x2="12" y2="12"></line>
                <line x1="12" y1="16" x2="12.01" y2="16"></line>
              </svg>
              <h3 class="empty-state-title">Unable to load requests</h3>
              <p class="empty-state-desc">${escapeHtml(err.message || 'Please check your connection.')}</p>
              <button type="button" class="btn btn-primary" id="retry-btn">Retry</button>
            </div>
          `;
          tableWrapper.querySelector('#retry-btn')?.addEventListener('click', () => loadData(true));
          return;
        }
      }
    }

    // Prominent offline warning banner
    if (isOffline) {
      if (bannerContainer) {
        bannerContainer.innerHTML = `<div class="banner banner-warning"><span class="icon">⚠️</span> You are currently offline. Displaying cached read-only requests. Changes cannot be submitted until connection is restored.</div>`;
      }
      if (newRequestBtn) {
        newRequestBtn.classList.add('disabled');
        newRequestBtn.style.opacity = '0.6';
        newRequestBtn.style.pointerEvents = 'none';
        newRequestBtn.title = 'Offline mode: Changes cannot be submitted until connection is restored.';
      }
    } else {
      if (bannerContainer) {
        bannerContainer.innerHTML = `
          <div id="requests-sse-badge" style="display: flex; align-items: center; gap: var(--space-2); margin-bottom: var(--space-4); font-size: var(--text-xs); color: var(--text-muted);">
            <span class="badge-dot" style="background-color: var(--success); width: 8px; height: 8px;"></span>
            <span>Live updates connected via tenant stream</span>
          </div>
        `;
      }
      if (newRequestBtn) {
        newRequestBtn.classList.remove('disabled');
        newRequestBtn.style.opacity = '';
        newRequestBtn.style.pointerEvents = '';
        newRequestBtn.removeAttribute('title');
      }
    }

      // Normalize row data
      const normalizedData = items.map(req => {
        const payload = req.payload || {};
        const id = String(req.id || '');
        const vendor = req.vendor_name || payload.vendor_name || payload.vendor || 'Unknown Vendor';
        const amountRaw = req.amount !== undefined && req.amount !== null
          ? req.amount
          : (payload.estimated_spend || payload.raw_amount_str || payload.amount || 0);
        const amount = Number(amountRaw).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
        const currency = req.currency || payload.currency || payload.raw_currency_str || 'USD';
        const status = req.status || 'draft';
        const createdAt = req.created_at ? new Date(req.created_at).toLocaleString() : 'Recent';

        return {
          id,
          vendor,
          amount,
          currency,
          status,
          created_at: createdAt
        };
      });

      if (!dataTable) {
        dataTable = new DataTable({
          container: tableWrapper,
          title: 'Requests Registry',
          initialSortKey: 'created_at',
          initialSortDirection: 'desc',
          emptyMessage: 'No procurement requests found. Click "Submit New Request" to create your first request.',
          columns: [
            {
              key: 'id',
              label: 'Request ID',
              sortable: true,
              render: (val, row) => `
                <a href="#/app/requests/${escapeHtml(row.id)}" style="font-family: var(--font-mono); font-weight: var(--font-semibold); color: var(--brand-primary); text-decoration: none;">
                  #${escapeHtml(String(val).slice(0, 8))}
                </a>
              `
            },
            {
              key: 'vendor',
              label: 'Vendor / Tool',
              sortable: true,
              render: (val) => `<span style="font-weight: var(--font-medium);">${escapeHtml(val)}</span>`
            },
            {
              key: 'amount',
              label: 'Amount',
              sortable: true,
              render: (val, row) => `<span style="font-weight: var(--font-semibold);">${escapeHtml(val)}</span>`
            },
            {
              key: 'currency',
              label: 'Currency',
              sortable: true,
              render: (val) => `<span class="badge badge-neutral">${escapeHtml(val)}</span>`
            },
            {
              key: 'status',
              label: 'Status',
              sortable: true,
              render: (val) => {
                const s = String(val).toLowerCase();
                let badgeClass = 'badge-neutral';
                if (s === 'approved') badgeClass = 'badge-compliant';
                else if (s === 'rejected') badgeClass = 'badge-danger';
                else if (s === 'in_review' || s === 'awaiting_human' || s === 'pending') badgeClass = 'badge-warning';
                else if (s === 'draft') badgeClass = 'badge-info';

                return `<span class="badge ${badgeClass}"><span class="badge-dot"></span>${escapeHtml(val)}</span>`;
              }
            },
            {
              key: 'created_at',
              label: 'Created At',
              sortable: true
            },
            {
              key: 'actions',
              label: 'Action',
              sortable: false,
              render: (_, row) => `
                <a href="#/app/requests/${escapeHtml(row.id)}" class="btn btn-secondary btn-sm" aria-label="View details for request #${escapeHtml(row.id.slice(0, 8))}">
                  View Details
                </a>
              `
            }
          ],
          data: normalizedData
        });
      } else {
        dataTable.setData(normalizedData);
      }

    if (showToast) {
      if (isOffline) {
        toast.warning('Offline: Loaded cached requests.');
      } else {
        toast.info('Requests list refreshed.');
      }
    }
  }

  // Initial Load
  await loadData(false);

  // Manual Refresh
  refreshBtn.addEventListener('click', () => {
    refreshBtn.classList.add('btn-loading');
    loadData(true).finally(() => refreshBtn.classList.remove('btn-loading'));
  });

  // Hook up to live SSE updates for instant status refresh
  activeSseUnsubscribe = state.subscribeToTenantStream((event) => {
    // Re-fetch on any request creation, status transition, or approval update
    if (
      event.event === 'request.created' ||
      event.event === 'request_created' ||
      event.event === 'request.updated' ||
      event.event === 'request_updated' ||
      event.event === 'approval.decided' ||
      event.event === 'status_updated' ||
      event.event === 'message'
    ) {
      loadData(false);
    }
  });
}
