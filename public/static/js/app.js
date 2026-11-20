/**
 * Omnea Application Shell & Root Controller (app.js)
 * Modular application entrypoint loaded via script type="module".
 * Enforces zero inline scripts, responsive shell layout, role-based navigation,
 * real-time notification dropdown, command palette trigger, and theme sync.
 */

import { state, escapeHtml } from './state.js';
import { router } from './router.js';
import { commandPalette } from './components/command_palette.js';
import { toast } from './components/toast.js';
import { authApi } from './api.js';

// Role-specific sidebar navigation configurations
const ROLE_SIDEBAR_NAV = {
  requester: [
    { title: 'Procurement', items: [
      { path: '/app/requests', label: 'My Requests', icon: 'list' },
      { path: '/app/requests/new', label: 'Submit Request', icon: 'plus-circle' },
      { path: '/app/vendors', label: 'Vendors', icon: 'grid' },
      { path: '/app/requests/status', label: 'Request Status', icon: 'activity' },
      { path: '/app/catalog', label: 'Catalog', icon: 'grid' }
    ]}
  ],
  approver: [
    { title: 'Approvals', items: [
      { path: '/app/approvals', label: 'Pending Approvals', icon: 'check-square' },
      { path: '/app/vendors', label: 'Vendors', icon: 'grid' },
      { path: '/app/approvals/inspector', label: 'Advice Inspector', icon: 'cpu' },
      { path: '/app/approvals/history', label: 'Approval History', icon: 'clock' }
    ]}
  ],
  finance_admin: [
    { title: 'Financial Controls', items: [
      { path: '/app/finance', label: 'Finance', icon: 'dollar-sign' },
      { path: '/app/vendors', label: 'Vendors', icon: 'grid' },
      { path: '/app/finance/budgets', label: 'Budgets & Spend Caps', icon: 'dollar-sign' },
      { path: '/app/finance/erp', label: 'ERP Reconciliation', icon: 'repeat' },
      { path: '/app/finance/orders', label: 'Purchase Orders', icon: 'file-text' }
    ]}
  ],
  security_reviewer: [
    { title: 'Security & Compliance', items: [
      { path: '/app/security', label: 'Security & TPRM', icon: 'shield' },
      { path: '/app/vendors', label: 'Vendors', icon: 'grid' },
      { path: '/app/security/reviews', label: 'Vendor Security', icon: 'shield' },
      { path: '/app/security/compliance', label: 'SOC2 Analysis', icon: 'award' },
      { path: '/app/security/tprm', label: 'TPRM Queue', icon: 'alert-triangle' }
    ]}
  ],
  legal_reviewer: [
    { title: 'Legal & Risk', items: [
      { path: '/app/legal', label: 'Legal', icon: 'file' },
      { path: '/app/vendors', label: 'Vendors', icon: 'grid' },
      { path: '/app/legal/contracts', label: 'Contract Reviews', icon: 'file' },
      { path: '/app/legal/terms', label: 'Terms & Liability', icon: 'alert-circle' },
      { path: '/app/legal/queue', label: 'Legal Approval Queue', icon: 'inbox' }
    ]}
  ],
  cfo: [
    { title: 'Finance Leadership', items: [
      { path: '/app/finance', label: 'Finance', icon: 'dollar-sign' },
      { path: '/app/vendors', label: 'Vendors', icon: 'grid' }
    ]}
  ],
  tenant_admin: [
    { title: 'Administration', items: [
      { path: '/app/admin/org', label: 'Organization Settings', icon: 'settings' },
      { path: '/app/admin/developers', label: 'Developer Platform', icon: 'cpu' },
      { path: '/app/admin/users', label: 'User & Role Mgmt', icon: 'users' },
      { path: '/app/vendors', label: 'Vendors', icon: 'grid' },
      { path: '/app/finance', label: 'Finance', icon: 'dollar-sign' },
      { path: '/app/legal', label: 'Legal', icon: 'file' },
      { path: '/app/security', label: 'Security & TPRM', icon: 'shield' },
      { path: '/app/admin/invitations', label: 'Invitations', icon: 'mail' },
      { path: '/app/admin/audit', label: 'Audit Chain Log', icon: 'link' },
      { path: '/app/admin/policies', label: 'Policy Engine Config', icon: 'sliders' }
    ]}
  ]
};

// SVG icon dictionary
function getNavIconSvg(name) {
  switch (name) {
    case 'list': return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line><line x1="3" y1="6" x2="3.01" y2="6"></line><line x1="3" y1="12" x2="3.01" y2="12"></line><line x1="3" y1="18" x2="3.01" y2="18"></line></svg>';
    case 'plus-circle': return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="16"></line><line x1="8" y1="12" x2="16" y2="12"></line></svg>';
    case 'activity': return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>';
    case 'grid': return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></svg>';
    case 'check-square': return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 11 12 14 22 4"></polyline><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"></path></svg>';
    case 'cpu': return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2" ry="2"></rect><rect x="9" y="9" width="6" height="6"></rect><line x1="9" y1="1" x2="9" y2="4"></line><line x1="15" y1="1" x2="15" y2="4"></line><line x1="9" y1="20" x2="9" y2="23"></line><line x1="15" y1="20" x2="15" y2="23"></line><line x1="20" y1="9" x2="23" y2="9"></line><line x1="20" y1="14" x2="23" y2="14"></line><line x1="1" y1="9" x2="4" y1="9"></line><line x1="1" y1="14" x2="4" y1="14"></line></svg>';
    case 'clock': return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>';
    case 'dollar-sign': return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"></line><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"></path></svg>';
    case 'repeat': return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"></polyline><path d="M3 11V9a4 4 0 0 1 4-4h14"></path><polyline points="7 23 3 19 7 15"></polyline><path d="M21 13v2a4 4 0 0 1-4 4H3"></path></svg>';
    case 'file-text': return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>';
    case 'shield': return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>';
    case 'award': return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="7"></circle><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"></polyline></svg>';
    case 'alert-triangle': return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>';
    case 'file': return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"></path><polyline points="13 2 13 9 20 9"></polyline></svg>';
    case 'alert-circle': return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>';
    case 'inbox': return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"></polyline><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"></path></svg>';
    case 'settings': return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>';
    case 'users': return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M23 21v-2a4 4 0 0 0-3-3.87"></path><path d="M16 3.13a4 4 0 0 1 0 7.75"></path></svg>';
    case 'mail': return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path><polyline points="22,6 12,13 2,6"></polyline></svg>';
    case 'link': return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path></svg>';
    case 'sliders': return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="21" x2="4" y2="14"></line><line x1="4" y1="10" x2="4" y2="3"></line><line x1="12" y1="21" x2="12" y2="12"></line><line x1="12" y1="8" x2="12" y2="3"></line><line x1="20" y1="21" x2="20" y2="16"></line><line x1="20" y1="12" x2="20" y2="3"></line><line x1="1" y1="14" x2="7" y2="14"></line><line x1="9" y1="8" x2="15" y2="8"></line><line x1="17" y1="16" x2="23" y2="16"></line></svg>';
    default: return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle></svg>';
  }
}

class AppController {
  constructor() {
    this.root = document.getElementById('app-root');
    this.currentMode = null; // 'public' | 'app'
    this._init();
    this._setupDesktopBridge();
  }

  _init() {
    router.init((cleanPath, params) => {
      this._handleRouteChange(cleanPath, params);
    });
  }

  _setupDesktopBridge() {
    if (window.procureDesktop) {
      window.procureDesktop.onDeepLink?.((data) => {
        if (data?.id) window.location.hash = `#/app/requests/${data.id}`;
      });
      this.syncApprovalBadge();

      // Subscribe to tenant SSE events to keep desktop badge count in sync
      state.subscribeToTenantStream((event) => {
        if (
          event.event === 'approval_request' ||
          event.event === 'approval.decided' ||
          event.event === 'approval_updated' ||
          event.event === 'status_updated'
        ) {
          this.syncApprovalBadge();
        }
      });
    }
  }

  async syncApprovalBadge(explicitCount) {
    if (!window.procureDesktop?.setBadgeCount) return;
    if (explicitCount !== undefined) {
      window.procureDesktop.setBadgeCount(explicitCount);
      return;
    }
    if (!state.isAuthenticated()) {
      window.procureDesktop.setBadgeCount(0);
      return;
    }
    try {
      const { approvalsApi } = await import('./api.js');
      const res = await approvalsApi.getPending().catch(() => null);
      if (res) {
        const items = Array.isArray(res) ? res : (res.pending_approvals || res.items || []);
        const pendingCount = items.length;
        window.procureDesktop.setBadgeCount(pendingCount);
      }
    } catch {
      // ignore
    }
  }

  _handleRouteChange(cleanPath, params) {
    const isAppPath = cleanPath.startsWith('/app');
    const targetMode = isAppPath ? 'app' : 'public';

    if (this.currentMode !== targetMode) {
      this.currentMode = targetMode;
      if (targetMode === 'app') {
        this._renderAppShell();
      } else {
        this._renderPublicShell();
      }
    }

    if (targetMode === 'app') {
      this._updateActiveNav(cleanPath);
      const contentContainer = document.getElementById('app-content-area');
      if (contentContainer) {
        router.renderView(cleanPath, params, contentContainer);
      }
    } else {
      const publicContainer = document.getElementById('public-content-area');
      if (publicContainer) {
        router.renderView(cleanPath, params, publicContainer);
      }
    }
  }

  _renderPublicShell() {
    this.root.innerHTML = `<div id="public-content-area"></div>`;
  }

  _renderAppShell() {
    const tenantId = state.getTenantId() || 'Acme Tenant';
    const activeRole = state.getActiveRole();
    const user = state.getUser() || { email: 'user@omnea.ai' };
    const userRoles = state.getRoles();

    this.root.innerHTML = `
      <div class="app-shell">
        <!-- Sidebar Navigation Drawer -->
        <aside class="app-sidebar" id="app-sidebar">
          <div class="sidebar-header">
            <a href="#/app" class="sidebar-logo">
              <div class="sidebar-logo-icon">Ω</div>
              <span>Omnea</span>
            </a>
          </div>
          <div class="sidebar-content" id="sidebar-nav-container"></div>
          <div class="sidebar-footer">
            <div class="tenant-pill">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect>
                <rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect>
                <line x1="6" y1="6" x2="6.01" y2="6"></line>
                <line x1="6" y1="18" x2="6.01" y2="18"></line>
              </svg>
              <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(tenantId)}</span>
            </div>
          </div>
        </aside>

        <!-- Main Workspace -->
        <div class="app-main">
          <!-- App Header -->
          <header class="app-header">
            <div class="header-left">
              <button type="button" class="mobile-toggle" id="mobile-menu-btn" aria-label="Toggle navigation drawer">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <line x1="3" y1="12" x2="21" y2="12"></line>
                  <line x1="3" y1="6" x2="21" y2="6"></line>
                  <line x1="3" y1="18" x2="21" y2="18"></line>
                </svg>
              </button>

              <div class="header-badge-group">
                <span class="badge badge-info" id="current-role-badge">
                  Role: ${escapeHtml(activeRole)}
                </span>
                ${userRoles.length > 1 ? `
                  <select id="role-switcher-select" class="form-select" style="padding: 2px 8px; font-size: 11px; height: 26px;">
                    ${userRoles.map(r => `<option value="${escapeHtml(r)}" ${r === activeRole ? 'selected' : ''}>Switch to ${escapeHtml(r)}</option>`).join('')}
                  </select>
                ` : ''}
              </div>
            </div>

            <div class="header-right">
              <!-- Desktop Server Settings Action -->
              ${window.procureDesktop ? `
                <button type="button" class="btn btn-secondary btn-sm" id="desktop-server-settings-btn" title="Server Settings" aria-label="Server Settings" style="display: inline-flex; align-items: center; gap: var(--space-1); height: 32px; font-size: var(--text-xs);">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect>
                    <rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect>
                    <line x1="6" y1="6" x2="6.01" y2="6"></line>
                    <line x1="6" y1="18" x2="6.01" y2="18"></line>
                  </svg>
                  <span>Server Settings</span>
                </button>
              ` : ''}

              <!-- Command Palette Trigger -->
              <button type="button" class="cmd-palette-btn" id="cmd-palette-trigger" aria-label="Open command search">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <circle cx="11" cy="11" r="8"></circle>
                  <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                </svg>
                <span>Search...</span>
                <span class="kbd-badge">Ctrl+K</span>
              </button>

              <!-- Notifications Bell & Dropdown -->
              <div class="notification-wrapper">
                <button type="button" class="notification-bell-btn" id="notif-bell-btn" aria-label="View notifications">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"></path>
                    <path d="M13.73 21a2 2 0 0 1-3.46 0"></path>
                  </svg>
                  <span class="notification-count" id="notif-badge-count" style="display: none;">0</span>
                </button>

                <div class="notification-dropdown" id="notif-dropdown-panel">
                  <div class="notification-header">
                    <strong style="font-size: var(--text-sm);">Notifications</strong>
                    <button type="button" class="btn-link" id="mark-all-read-btn" style="font-size: 11px;">Mark all read</button>
                  </div>
                  <ul class="notification-list" id="notif-list-items"></ul>
                </div>
              </div>

              <!-- Theme Toggle -->
              <button type="button" class="theme-toggle-btn" id="app-theme-btn" aria-label="Toggle theme">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
                </svg>
              </button>

              <!-- User Profile Dropdown -->
              <div class="user-menu-wrapper">
                <button type="button" class="user-menu-btn" id="user-menu-btn" aria-label="User account menu">
                  <div class="user-avatar">${(user.email || 'U').slice(0, 2).toUpperCase()}</div>
                </button>

                <div class="user-menu-dropdown" id="user-dropdown-panel">
                  <div class="user-menu-header">
                    <div class="user-menu-email">${escapeHtml(user.email || '')}</div>
                    <div class="user-menu-role">${escapeHtml(activeRole)}</div>
                  </div>
                  <ul class="user-menu-list">
                    ${window.procureDesktop ? `
                      <li><button type="button" class="user-menu-item" id="menu-desktop-settings-btn">Server Settings</button></li>
                    ` : ''}
                    <li><button type="button" class="user-menu-item" id="menu-docs-btn">API Documentation</button></li>
                    <li><button type="button" class="user-menu-item logout-item" id="menu-logout-btn">Sign Out</button></li>
                  </ul>
                </div>
              </div>
            </div>
          </header>

          <!-- Dynamic Workspace Content Area -->
          <main class="app-content" id="app-content-area" role="main"></main>
        </div>
      </div>
    `;

    this._renderSidebarNavigation();
    this._attachHeaderListeners();
    this._syncNotificationBadge();
  }

  _renderSidebarNavigation() {
    const navContainer = document.getElementById('sidebar-nav-container');
    if (!navContainer) return;
    navContainer.innerHTML = '';

    const role = state.getActiveRole();
    const sections = ROLE_SIDEBAR_NAV[role] || ROLE_SIDEBAR_NAV.requester;

    sections.forEach(sec => {
      const secEl = document.createElement('div');
      secEl.innerHTML = `<div class="nav-section-title">${escapeHtml(sec.title)}</div>`;

      const ul = document.createElement('ul');
      ul.className = 'nav-list';

      sec.items.forEach(item => {
        const li = document.createElement('li');
        li.innerHTML = `
          <a href="#${item.path}" class="nav-link" data-path="${item.path}">
            ${getNavIconSvg(item.icon)}
            <span>${escapeHtml(item.label)}</span>
          </a>
        `;
        ul.appendChild(li);
      });

      secEl.appendChild(ul);
      navContainer.appendChild(secEl);
    });
  }

  _updateActiveNav(currentPath) {
    const links = document.querySelectorAll('.nav-link');
    links.forEach(link => {
      const linkPath = link.getAttribute('data-path');
      if (linkPath && (currentPath === linkPath || (linkPath !== '/app' && currentPath.startsWith(linkPath)))) {
        link.classList.add('is-active');
      } else {
        link.classList.remove('is-active');
      }
    });
  }

  _attachHeaderListeners() {
    // Mobile Drawer Toggle
    const mobileBtn = document.getElementById('mobile-menu-btn');
    const sidebar = document.getElementById('app-sidebar');
    if (mobileBtn && sidebar) {
      mobileBtn.addEventListener('click', () => {
        sidebar.classList.toggle('mobile-open');
      });
    }

    // Role Switcher
    const roleSelect = document.getElementById('role-switcher-select');
    if (roleSelect) {
      roleSelect.addEventListener('change', (e) => {
        state.setActiveRole(e.target.value);
        this._renderSidebarNavigation();
        router.navigate('/app');
      });
    }

    // Command Palette Trigger
    const cmdBtn = document.getElementById('cmd-palette-trigger');
    if (cmdBtn) {
      cmdBtn.addEventListener('click', () => commandPalette.open());
    }

    // Theme Switcher
    const themeBtn = document.getElementById('app-theme-btn');
    if (themeBtn) {
      themeBtn.addEventListener('click', () => state.toggleTheme());
    }

    // Notifications Bell & Panel
    const notifBtn = document.getElementById('notif-bell-btn');
    const notifPanel = document.getElementById('notif-dropdown-panel');
    const markAllBtn = document.getElementById('mark-all-read-btn');

    if (notifBtn && notifPanel) {
      notifBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        notifPanel.classList.toggle('is-open');
        this._renderNotificationList();
      });

      markAllBtn.addEventListener('click', () => {
        state.markAllNotificationsAsRead();
        this._renderNotificationList();
        this._syncNotificationBadge();
      });
    }

    // Desktop Server Settings Trigger
    const desktopSettingsBtn = document.getElementById('desktop-server-settings-btn');
    if (desktopSettingsBtn) {
      desktopSettingsBtn.addEventListener('click', () => {
        window.procureDesktop?.openServerSettings?.();
      });
    }

    const menuDesktopSettingsBtn = document.getElementById('menu-desktop-settings-btn');
    if (menuDesktopSettingsBtn) {
      menuDesktopSettingsBtn.addEventListener('click', () => {
        if (userPanel) userPanel.classList.remove('is-open');
        window.procureDesktop?.openServerSettings?.();
      });
    }

    // User Profile Dropdown
    const userBtn = document.getElementById('user-menu-btn');
    const userPanel = document.getElementById('user-dropdown-panel');
    const logoutBtn = document.getElementById('menu-logout-btn');
    const docsBtn = document.getElementById('menu-docs-btn');

    if (userBtn && userPanel) {
      userBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        userPanel.classList.toggle('is-open');
      });

      docsBtn.addEventListener('click', () => {
        window.open('/docs', '_blank');
      });

      logoutBtn.addEventListener('click', async () => {
        if (window.procureDesktop?.clearAuthTokens) {
          try {
            await window.procureDesktop.clearAuthTokens();
          } catch (e) {
            console.warn('Desktop clearAuthTokens error:', e);
          }
        }
        if (window.procureDesktop?.setBadgeCount) {
          try {
            window.procureDesktop.setBadgeCount(0);
          } catch (e) {
            // ignore
          }
        }
        await authApi.logout();
        toast.info('Signed out of Omnea console.');
        router.navigate('/login');
      });
    }

    // Close dropdowns on outside click
    document.addEventListener('click', () => {
      if (notifPanel) notifPanel.classList.remove('is-open');
      if (userPanel) userPanel.classList.remove('is-open');
    });

    // Subscribe to state notifications updates
    state.subscribe('notifications', () => {
      this._syncNotificationBadge();
      this._renderNotificationList();
    });
  }

  _syncNotificationBadge() {
    const badge = document.getElementById('notif-badge-count');
    if (!badge) return;
    const count = state.getState().unreadCount;
    if (count > 0) {
      badge.textContent = count > 9 ? '9+' : String(count);
      badge.style.display = 'flex';
    } else {
      badge.style.display = 'none';
    }
  }

  _renderNotificationList() {
    const list = document.getElementById('notif-list-items');
    if (!list) return;
    list.innerHTML = '';

    const notifications = state.getState().notifications;
    if (notifications.length === 0) {
      list.innerHTML = `<li style="padding: var(--space-6); text-align: center; color: var(--text-muted); font-size: var(--text-xs);">No notifications yet</li>`;
      return;
    }

    notifications.slice(0, 15).forEach(n => {
      const li = document.createElement('li');
      li.className = `notification-item ${n.is_read ? '' : 'is-unread'}`;
      li.innerHTML = `
        <div class="notification-item-text">
          <div style="font-weight: var(--font-semibold);">${escapeHtml(n.title)}</div>
          <div style="color: var(--text-secondary); margin-top: 1px;">${escapeHtml(n.message)}</div>
          <div class="notification-item-time">${escapeHtml(n.time)}</div>
        </div>
      `;

      li.addEventListener('click', () => {
        state.markNotificationAsRead(n.id);
        this._syncNotificationBadge();
        if (n.link) {
          router.navigate(n.link);
        }
      });

      list.appendChild(li);
    });
  }
}

// Instantiate application on DOM ready or immediately if already loaded
async function initApp() {
  if (!window.__omnea) {
    // Detect if running inside desktop container
    if (window.procureDesktop) {
      // Listen for deep-link events: window.procureDesktop.onDeepLink?.((data) => { if (data?.id) window.location.hash = `#/app/requests/${data.id}`; });
      window.procureDesktop.onDeepLink?.((data) => {
        if (data?.id) {
          window.location.hash = `#/app/requests/${data.id}`;
        }
      });

      // Handle token lifecycle: On app startup, if not authenticated in browser session, check getAuthTokens
      if (!state.isAuthenticated()) {
        try {
          const tokens = await window.procureDesktop.getAuthTokens?.();
          if (tokens && (tokens.accessToken || tokens.token)) {
            const accessToken = tokens.accessToken || tokens.token;
            const tenantId = tokens.tenantId || tokens.tenant_id || 'default';
            const email = tokens.email || 'user@omnea.ai';
            const user = tokens.user || {
              userId: tokens.userId || tokens.user_id || email,
              email: email,
              fullName: tokens.fullName || tokens.name || email
            };
            const roles = tokens.roles && tokens.roles.length > 0 ? tokens.roles : ['requester'];
            const csrfToken = tokens.csrfToken || tokens.csrf_token || '';
            state.setSession(accessToken, csrfToken, user, tenantId, roles, tokens.refreshToken);
          }
        } catch (err) {
          console.warn('Desktop auto-restore token error on startup:', err);
        }
      }
    }

    const app = new AppController();
    window.__omnea = { app, state, router, toast, commandPalette, authApi };
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApp);
} else {
  initApp();
}
