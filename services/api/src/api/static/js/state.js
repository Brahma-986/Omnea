/**
 * Omnea Frontend State Management (state.js)
 * Manages user auth, tenant context, active roles, theme preferences,
 * notifications, SSE event streaming, and pub/sub subscriptions.
 */

// Safe XSS Escaping Utility
export function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  const text = String(str);
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

class Store {
  constructor() {
    this._subscribers = new Map();
    this._state = {
      token: localStorage.getItem('omnea_token') || '',
      csrfToken: localStorage.getItem('omnea_csrf_token') || '',
      user: this._loadJson('omnea_user', null),
      tenantId: localStorage.getItem('omnea_tenant_id') || '',
      roles: this._loadJson('omnea_roles', []),
      activeRole: localStorage.getItem('omnea_active_role') || '',
      theme: localStorage.getItem('omnea_theme') || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'),
      notifications: this._loadJson('omnea_notifications', [
        {
          id: 'n-1',
          title: 'Security Review Completed',
          message: 'Datadog SOC2 analysis completed with compliant status.',
          time: '5m ago',
          is_read: false,
          link: '/app/security/reviews'
        },
        {
          id: 'n-2',
          title: 'Approval Request Assigned',
          message: 'Snowflake renewal requires finance budget approval.',
          time: '1h ago',
          is_read: false,
          link: '/app/approvals'
        }
      ]),
      unreadCount: 2,
      onboarding: this._loadJson('omnea_onboarding', {
        orgSetup: true,
        inviteTeam: false,
        configRules: false,
        connectErp: false
      })
    };

    // Apply theme on load
    this.setTheme(this._state.theme);
    this._sseSource = null;
    this._reconnectTimeout = null;
    this._tenantStreamListeners = new Set();

    if (this.isAuthenticated()) {
      this.initSseStream();
    }
  }

  _loadJson(key, defaultValue) {
    try {
      const val = localStorage.getItem(key);
      return val ? JSON.parse(val) : defaultValue;
    } catch {
      return defaultValue;
    }
  }

  getState() {
    return this._state;
  }

  setState(partial) {
    if (!partial || typeof partial !== 'object') return;
    Object.assign(this._state, partial);
    Object.keys(partial).forEach(key => this._notify(key));
  }

  subscribe(key, callback) {
    if (!this._subscribers.has(key)) {
      this._subscribers.set(key, new Set());
    }
    this._subscribers.get(key).add(callback);
    return () => this._subscribers.get(key).delete(callback);
  }

  _notify(key) {
    if (this._subscribers.has(key)) {
      const val = this._state[key];
      this._subscribers.get(key).forEach(cb => {
        try { cb(val); } catch (e) { console.error('Subscription error:', e); }
      });
    }
  }

  isAuthenticated() {
    return Boolean(this._state.token && this._state.user);
  }

  getToken() {
    return this._state.token;
  }

  getCsrfToken() {
    return this._state.csrfToken;
  }

  getTenantId() {
    return this._state.tenantId;
  }

  getUser() {
    return this._state.user;
  }

  getRoles() {
    return this._state.roles || [];
  }

  getActiveRole() {
    if (this._state.activeRole && this._state.roles.includes(this._state.activeRole)) {
      return this._state.activeRole;
    }
    return this._state.roles[0] || 'requester';
  }

  setActiveRole(role) {
    if (this._state.roles.includes(role)) {
      this._state.activeRole = role;
      localStorage.setItem('omnea_active_role', role);
      this._notify('activeRole');
    }
  }

  hasRole(role) {
    return this._state.roles.includes(role);
  }

  setSession(token, csrfToken, user, tenantId, roles, refreshToken = '') {
    this._state.token = token;
    this._state.csrfToken = csrfToken || '';
    this._state.user = user;
    this._state.tenantId = tenantId;
    this._state.roles = roles || [];
    this._state.activeRole = roles && roles.length > 0 ? roles[0] : 'requester';

    localStorage.setItem('omnea_token', token);
    localStorage.setItem('omnea_csrf_token', this._state.csrfToken);
    localStorage.setItem('omnea_user', JSON.stringify(user));
    localStorage.setItem('omnea_tenant_id', tenantId);
    localStorage.setItem('omnea_roles', JSON.stringify(roles || []));
    localStorage.setItem('omnea_active_role', this._state.activeRole);
    if (refreshToken) {
      localStorage.setItem('omnea_refresh_token', refreshToken);
    }

    // Sync auth tokens to desktop bridge
    if (window.procureDesktop?.saveAuthTokens) {
      try {
        const email = (user && user.email) ? user.email : '';
        window.procureDesktop.saveAuthTokens({
          accessToken: token,
          refreshToken: refreshToken || localStorage.getItem('omnea_refresh_token') || '',
          tenantId: tenantId,
          email: email
        });
      } catch (err) {
        console.warn('Desktop saveAuthTokens error in state:', err);
      }
    }

    this._notify('token');
    this._notify('user');
    this._notify('activeRole');

    this.initSseStream();
  }

  clearSession() {
    this._state.token = '';
    this._state.csrfToken = '';
    this._state.user = null;
    this._state.tenantId = '';
    this._state.roles = [];
    this._state.activeRole = '';

    localStorage.removeItem('omnea_token');
    localStorage.removeItem('omnea_csrf_token');
    localStorage.removeItem('omnea_refresh_token');
    localStorage.removeItem('omnea_user');
    localStorage.removeItem('omnea_tenant_id');
    localStorage.removeItem('omnea_roles');
    localStorage.removeItem('omnea_active_role');

    // Clear desktop tokens and reset badge
    if (window.procureDesktop?.clearAuthTokens) {
      try {
        window.procureDesktop.clearAuthTokens();
      } catch (err) {
        console.warn('Desktop clearAuthTokens error in state:', err);
      }
    }
    if (window.procureDesktop?.setBadgeCount) {
      try {
        window.procureDesktop.setBadgeCount(0);
      } catch (err) {
        // ignore
      }
    }

    if (this._sseSource) {
      this._sseSource.close();
      this._sseSource = null;
    }
    if (this._reconnectTimeout) {
      clearTimeout(this._reconnectTimeout);
      this._reconnectTimeout = null;
    }

    this._notify('token');
    this._notify('user');
  }

  clearAuth() {
    this.clearSession();
  }

  setTheme(theme) {
    const nextTheme = theme === 'dark' ? 'dark' : 'light';
    this._state.theme = nextTheme;
    localStorage.setItem('omnea_theme', nextTheme);
    document.documentElement.setAttribute('data-theme', nextTheme);
    this._notify('theme');
  }

  toggleTheme() {
    this.setTheme(this._state.theme === 'dark' ? 'light' : 'dark');
  }

  // Real-Time Notification Management
  addNotification(notification) {
    const item = {
      id: notification.id || 'notif-' + Date.now(),
      title: notification.title || 'Notification',
      message: notification.message || '',
      time: notification.time || 'Just now',
      is_read: false,
      link: notification.link || '/app'
    };
    this._state.notifications.unshift(item);
    this._recomputeUnreadCount();
    this._persistNotifications();
    this._notify('notifications');
  }

  markNotificationAsRead(id) {
    const item = this._state.notifications.find(n => n.id === id);
    if (item && !item.is_read) {
      item.is_read = true;
      this._recomputeUnreadCount();
      this._persistNotifications();
      this._notify('notifications');
    }
  }

  markAllNotificationsAsRead() {
    this._state.notifications.forEach(n => { n.is_read = true; });
    this._recomputeUnreadCount();
    this._persistNotifications();
    this._notify('notifications');
  }

  _recomputeUnreadCount() {
    this._state.unreadCount = this._state.notifications.filter(n => !n.is_read).length;
    this._notify('unreadCount');
  }

  _persistNotifications() {
    localStorage.setItem('omnea_notifications', JSON.stringify(this._state.notifications.slice(0, 50)));
  }

  // Onboarding Step Management
  completeOnboardingStep(stepKey) {
    if (this._state.onboarding[stepKey] !== undefined) {
      this._state.onboarding[stepKey] = true;
      localStorage.setItem('omnea_onboarding', JSON.stringify(this._state.onboarding));
      this._notify('onboarding');
    }
  }

  // Server-Sent Events (SSE) Stream & Tenant Subscriptions
  subscribeToTenantStream(callback) {
    if (typeof callback !== 'function') return () => {};
    this._tenantStreamListeners.add(callback);
    return () => {
      this._tenantStreamListeners.delete(callback);
    };
  }

  _notifyTenantStream(eventPayload) {
    this._tenantStreamListeners.forEach(cb => {
      try {
        cb(eventPayload);
      } catch (err) {
        console.error('Tenant stream listener error:', err);
      }
    });
  }

  async initSseStream() {
    if (this._sseSource) {
      this._sseSource.close();
      this._sseSource = null;
    }

    if (!this.isAuthenticated()) return;

    try {
      let streamUrl = '/api/v1/events/stream';
      const token = this.getToken();
      if (token) {
        try {
          const res = await fetch('/api/v1/events/ticket', {
            method: 'POST',
            headers: {
              'Authorization': `Bearer ${token}`,
              'Content-Type': 'application/json'
            }
          });
          if (res.ok) {
            const data = await res.json();
            if (data && data.ticket) {
              streamUrl = `/api/v1/events/stream?ticket=${encodeURIComponent(data.ticket)}`;
            }
          }
        } catch {
          // Fall back to same-origin cookie auth
        }
      }

      this._sseSource = new EventSource(streamUrl);

      this._sseSource.onmessage = (event) => {
        let parsed = null;
        try {
          parsed = JSON.parse(event.data);
          this.addNotification({
            title: parsed.title || 'Workflow Event',
            message: parsed.message || JSON.stringify(parsed),
            time: 'Just now',
            link: parsed.link || '/app'
          });
        } catch {
          // Fallback plain message
          this.addNotification({
            title: 'Procurement Update',
            message: event.data,
            time: 'Just now'
          });
        }
        this._notifyTenantStream({ event: 'message', data: parsed || event.data });
      };

      const customEvents = [
        'request.created',
        'request_created',
        'request.updated',
        'request_updated',
        'approval_request',
        'approval.decided',
        'approval_updated',
        'agent_step',
        'status_updated',
        'comment.created',
        'connected'
      ];

      customEvents.forEach(eventType => {
        this._sseSource.addEventListener(eventType, (event) => {
          let data = null;
          try {
            data = JSON.parse(event.data);
          } catch {
            data = event.data;
          }
          if (eventType === 'approval_request' && data) {
            this.addNotification({
              title: 'Action Required: Approval',
              message: `Request #${data.request_id || ''} requires your review`,
              link: '/app/approvals'
            });
          }
          this._notifyTenantStream({ event: eventType, data });
        });
      });

      this._sseSource.onerror = () => {
        if (this._sseSource) {
          this._sseSource.close();
          this._sseSource = null;
        }
        // Graceful exponential reconnect
        this._reconnectTimeout = setTimeout(() => {
          if (this.isAuthenticated()) {
            this.initSseStream();
          }
        }, 15000);
      };
    } catch (e) {
      console.warn('SSE stream initialization failed or unsupported:', e);
    }
  }
}

export const state = new Store();
