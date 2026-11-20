/**
 * Omnea Client-Side Router & Role-Based Route Guard (router.js)
 * Enforces authentication invariants (unauthenticated -> /login),
 * role-based route guards (unauthorized -> /app with 403 toast error),
 * and dynamic view rendering.
 */

import { state } from './state.js';
import { toast } from './components/toast.js';
import { renderPublicHome } from './views/public_home.js';
import {
  renderLogin,
  renderSignup,
  renderForgotPassword,
  renderResetPassword,
  renderAcceptInvite
} from './views/auth_views.js';
import { renderDashboard } from './views/dashboard_views.js';
import { renderIntakeChat } from './views/intake_chat.js';
import { renderRequestsList } from './views/requests_list.js';
import { renderRequestDetail } from './views/request_detail.js';
import { renderApproverInbox } from './views/approver_inbox.js';
import { renderVendorView } from './views/vendor_views.js';
import { renderFinanceView } from './views/finance_views.js';
import { renderLegalView } from './views/legal_views.js';
import { renderSecurityView } from './views/security_views.js';

// Role permission mapping for app routes
const ROUTE_ROLE_PERMISSIONS = [
  { pattern: /^\/app\/admin(\/.*)?$/, roles: ['tenant_admin'] },
  { pattern: /^\/app\/finance(\/.*)?$/, roles: ['finance_admin', 'tenant_admin', 'cfo'] },
  { pattern: /^\/app\/security(\/.*)?$/, roles: ['security_reviewer', 'tenant_admin'] },
  { pattern: /^\/app\/legal(\/.*)?$/, roles: ['legal_reviewer', 'tenant_admin'] },
  { pattern: /^\/app\/vendors(\/.*)?$/, roles: ['requester', 'approver', 'finance_admin', 'security_reviewer', 'legal_reviewer', 'tenant_admin', 'cfo'] },
  { pattern: /^\/app\/approvals(\/.*)?$/, roles: ['approver', 'tenant_admin'] },
  { pattern: /^\/app\/requests(\/.*)?$/, roles: ['requester', 'approver', 'finance_admin', 'security_reviewer', 'legal_reviewer', 'tenant_admin'] },
  { pattern: /^\/app\/catalog(\/.*)?$/, roles: ['requester', 'approver', 'finance_admin', 'security_reviewer', 'legal_reviewer', 'tenant_admin'] },
  { pattern: /^\/app(\/.*)?$/, roles: ['requester', 'approver', 'finance_admin', 'security_reviewer', 'legal_reviewer', 'tenant_admin', 'cfo'] }
];

class Router {
  constructor() {
    this.currentPath = '';
    this.onRouteChanged = null;
  }

  init(onRouteChangedCallback) {
    this.onRouteChanged = onRouteChangedCallback;

    window.addEventListener('hashchange', () => this.handleRoute());
    window.addEventListener('popstate', () => this.handleRoute());

    this.handleRoute();
  }

  getPath() {
    // Read from hash (e.g. #/app/requests) or fallback to pathname
    const hash = window.location.hash.slice(1);
    if (hash) {
      return hash.split('?')[0] || '/';
    }
    const path = window.location.pathname;
    return path === '' ? '/' : path;
  }

  getQueryParams() {
    const hash = window.location.hash.slice(1);
    const queryString = hash.includes('?') ? hash.split('?')[1] : window.location.search.slice(1);
    const params = {};
    if (queryString) {
      new URLSearchParams(queryString).forEach((val, key) => {
        params[key] = val;
      });
    }
    return params;
  }

  navigate(path) {
    if (window.location.hash !== `#${path}`) {
      window.location.hash = `#${path}`;
    } else {
      this.handleRoute();
    }
  }

  handleRoute() {
    const rawPath = this.getPath();
    const cleanPath = rawPath.startsWith('/') ? rawPath : `/${rawPath}`;
    this.currentPath = cleanPath;
    const params = this.getQueryParams();

    const isAuth = state.isAuthenticated();

    // 1. Invariant: Nothing usable in /app before login
    if (cleanPath.startsWith('/app')) {
      if (!isAuth) {
        toast.warning('Authentication required. Please sign in to access the platform.');
        this.navigate('/login');
        return;
      }

      // 2. Client route guard: Role authorization check
      const userRole = state.getActiveRole();
      const matchedRule = ROUTE_ROLE_PERMISSIONS.find(rule => rule.pattern.test(cleanPath));

      if (matchedRule && !matchedRule.roles.includes(userRole)) {
        // Forbidden route for current role
        toast.error('Access Denied: Role unauthorized');
        this.navigate('/app');
        return;
      }
    }

    // Redirect authenticated users away from public auth pages to /app
    if (isAuth && ['/login', '/signup', '/forgot-password'].includes(cleanPath)) {
      this.navigate('/app');
      return;
    }

    if (typeof this.onRouteChanged === 'function') {
      this.onRouteChanged(cleanPath, params);
    }
  }

  renderView(cleanPath, params, container) {
    container.innerHTML = '';

    if (cleanPath === '/' || cleanPath === '') {
      renderPublicHome(container);
      return;
    }

    if (cleanPath === '/login') {
      renderLogin(container);
      return;
    }

    if (cleanPath === '/signup') {
      renderSignup(container);
      return;
    }

    if (cleanPath === '/forgot-password') {
      renderForgotPassword(container);
      return;
    }

    if (cleanPath === '/reset-password') {
      renderResetPassword(container, params);
      return;
    }

    if (cleanPath === '/accept-invite') {
      renderAcceptInvite(container, params);
      return;
    }

    // Stage 3 Primary Views & Routes
    if (cleanPath === '/app/requests/new') {
      renderIntakeChat(container);
      return;
    }

    if (cleanPath === '/app/requests') {
      renderRequestsList(container);
      return;
    }

    const requestDetailMatch = cleanPath.match(/^\/app\/requests\/([a-zA-Z0-9_-]+)$/);
    if (requestDetailMatch) {
      const requestId = requestDetailMatch[1];
      renderRequestDetail(container, requestId);
      return;
    }

    if (cleanPath === '/app/approvals') {
      renderApproverInbox(container);
      return;
    }

    // Stage 4 Primary Views & Routes
    if (cleanPath.startsWith('/app/vendors')) {
      const subRoute = cleanPath.replace('/app/vendors', '') || '/';
      renderVendorView(container, subRoute);
      return;
    }

    if (cleanPath.startsWith('/app/finance')) {
      const subRoute = cleanPath.replace('/app/finance', '') || '/';
      renderFinanceView(container, subRoute);
      return;
    }

    if (cleanPath.startsWith('/app/legal')) {
      const subRoute = cleanPath.replace('/app/legal', '') || '/';
      renderLegalView(container, subRoute);
      return;
    }

    // Stage 5 Primary Views & Routes
    if (cleanPath.startsWith('/app/security')) {
      renderSecurityView(container, 'documents');
      return;
    }

    if (cleanPath.startsWith('/app')) {
      const subRoute = cleanPath.replace('/app', '') || '/';
      renderDashboard(container, subRoute);
      return;
    }

    // Default 404 handler
    container.innerHTML = `
      <div class="empty-state" style="margin-top: 10vh;">
        <svg class="empty-state-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"></circle>
          <line x1="12" y1="8" x2="12" y2="12"></line>
          <line x1="12" y1="16" x2="12.01" y2="16"></line>
        </svg>
        <h2 class="empty-state-title">Page Not Found</h2>
        <p class="empty-state-desc">The requested route "${cleanPath}" does not exist in the Omnea console.</p>
        <a href="#/" class="btn btn-primary">Return to Home</a>
      </div>
    `;
  }
}

export const router = new Router();
