/**
 * Omnea Procurement Requests Views (requests_views.js)
 * Stage 8 Desktop Bridge & Offline UI Integration.
 * Re-exports requests list & detail views, and provides offline cache handlers.
 */

import { escapeHtml } from '../state.js';
import { renderRequestsList } from './requests_list.js';
import { renderRequestDetail } from './request_detail.js';

export { renderRequestsList, renderRequestDetail };

export const OFFLINE_WARNING_BANNER = '<div class="banner banner-warning"><span class="icon">⚠️</span> You are currently offline. Displaying cached read-only requests. Changes cannot be submitted until connection is restored.</div>';

/**
 * Dispatches caching of requests to the Electron desktop bridge.
 */
export function cacheOfflineRequests(items) {
  if (window.procureDesktop?.saveOfflineRequests) {
    try {
      window.procureDesktop.saveOfflineRequests(items);
    } catch (err) {
      console.warn('Failed to save offline requests to desktop cache:', err);
    }
  }
}

/**
 * Retrieves cached read-only requests from the Electron desktop bridge.
 */
export async function getCachedOfflineRequests() {
  if (window.procureDesktop?.getOfflineRequests) {
    try {
      return await window.procureDesktop.getOfflineRequests();
    } catch (err) {
      console.warn('Failed to get offline requests from desktop cache:', err);
      return [];
    }
  }
  return [];
}

/**
 * Displays offline warning banner and renders cached requests safely.
 */
export async function renderOfflineRequestsFallback(container) {
  const cached = await getCachedOfflineRequests();
  container.innerHTML = `
    <div class="banner banner-warning"><span class="icon">⚠️</span> You are currently offline. Displaying cached read-only requests. Changes cannot be submitted until connection is restored.</div>
    <div id="requests-offline-list">
      ${cached.map(req => `
        <div class="card" style="margin-bottom: var(--space-3); padding: var(--space-3);">
          <div style="font-weight: var(--font-semibold);">${escapeHtml(req.vendor_name || req.vendor || 'Unknown Vendor')}</div>
          <div style="font-size: var(--text-sm); color: var(--text-secondary);">${escapeHtml(req.amount || 0)} ${escapeHtml(req.currency || 'USD')} - Status: ${escapeHtml(req.status || 'cached')}</div>
        </div>
      `).join('')}
    </div>
  `;
}
