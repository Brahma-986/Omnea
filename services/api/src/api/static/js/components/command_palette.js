/**
 * Omnea Command Palette Component (command_palette.js)
 * Global accessible search modal activated via Ctrl+K / Cmd+K.
 * Fuzzy searches navigation routes, quick actions, and procurement tasks.
 */

import { state } from '../state.js';
import { router } from '../router.js';

export class CommandPalette {
  constructor() {
    this.isOpen = false;
    this.activeIndex = 0;
    this.results = [];
    this._handleKeyDown = this._handleKeyDown.bind(this);
    this._initDom();
    this._registerGlobalShortcuts();
  }

  _initDom() {
    const backdrop = document.createElement('div');
    backdrop.className = 'cmd-palette-backdrop';
    backdrop.id = 'command-palette-modal';
    backdrop.setAttribute('role', 'dialog');
    backdrop.setAttribute('aria-modal', 'true');
    backdrop.setAttribute('aria-label', 'Global Command Palette');

    backdrop.innerHTML = `
      <div class="cmd-palette-dialog">
        <div class="cmd-palette-search">
          <svg class="cmd-palette-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="8"></circle>
            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
          <input type="text" id="palette-search-input" class="cmd-palette-input" placeholder="Type a command, route, or search query..." aria-label="Command search">
          <span class="kbd-badge">ESC to close</span>
        </div>
        <ul class="cmd-palette-results" role="listbox"></ul>
        <div class="cmd-palette-footer">
          <span>Navigation: <span class="kbd-badge">↑</span> <span class="kbd-badge">↓</span></span>
          <span>Execute: <span class="kbd-badge">↵</span></span>
        </div>
      </div>
    `;

    this.backdrop = backdrop;
    this.input = backdrop.querySelector('.cmd-palette-input');
    this.resultsList = backdrop.querySelector('.cmd-palette-results');

    // Close on backdrop click
    backdrop.addEventListener('click', (e) => {
      if (e.target === backdrop) this.close();
    });

    // Input search typing
    this.input.addEventListener('input', (e) => {
      this._filterCommands(e.target.value.trim());
    });

    if (document.body) {
      document.body.appendChild(backdrop);
    } else {
      document.addEventListener('DOMContentLoaded', () => {
        document.body.appendChild(backdrop);
      });
    }
  }

  _registerGlobalShortcuts() {
    window.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K' || e.code === 'KeyK' || (e.key && e.key.toLowerCase() === 'k'))) {
        e.preventDefault();
        this.toggle();
      }
    });

    document.addEventListener('keydown', this._handleKeyDown);
  }

  _getCommands() {
    const role = state.getActiveRole();
    const isAuth = state.isAuthenticated();

    const commands = [];

    if (!isAuth) {
      commands.push(
        { category: 'Public Views', title: 'Home Page', action: () => router.navigate('/') },
        { category: 'Public Views', title: 'Sign In', action: () => router.navigate('/login') },
        { category: 'Public Views', title: 'Sign Up', action: () => router.navigate('/signup') },
        { category: 'Documentation', title: 'API Documentation (/docs)', action: () => window.open('/docs', '_blank') }
      );
      return commands;
    }

    // Role-specific and Common App Navigation
    commands.push(
      { category: 'Quick Actions', title: 'Submit New Request', action: () => router.navigate('/app/requests/new') },
      { category: 'Quick Actions', title: 'Toggle Light / Dark Mode', action: () => state.toggleTheme() },
      { category: 'Quick Actions', title: 'View Documentation', action: () => window.open('/docs', '_blank') }
    );

    // Requester routes
    commands.push(
      { category: 'Procurement', title: 'My Requests', action: () => router.navigate('/app/requests') },
      { category: 'Procurement', title: 'Catalog', action: () => router.navigate('/app/catalog') },
      { category: 'Procurement', title: 'Request Status', action: () => router.navigate('/app/requests/status') }
    );

    // Approver routes
    if (['approver', 'tenant_admin'].includes(role)) {
      commands.push(
        { category: 'Approvals', title: 'Pending Approvals Queue', action: () => router.navigate('/app/approvals') },
        { category: 'Approvals', title: 'Multi-Agent Advice Inspector', action: () => router.navigate('/app/approvals/inspector') },
        { category: 'Approvals', title: 'Approval History', action: () => router.navigate('/app/approvals/history') }
      );
    }

    // Finance routes
    if (['finance_admin', 'tenant_admin'].includes(role)) {
      commands.push(
        { category: 'Finance', title: 'Budgets & Spend Caps', action: () => router.navigate('/app/finance/budgets') },
        { category: 'Finance', title: 'ERP Reconciliation', action: () => router.navigate('/app/finance/erp') },
        { category: 'Finance', title: 'Purchase Orders', action: () => router.navigate('/app/finance/orders') }
      );
    }

    // Security routes
    if (['security_reviewer', 'tenant_admin'].includes(role)) {
      commands.push(
        { category: 'Security & Risk', title: 'Vendor Security Reviews', action: () => router.navigate('/app/security/reviews') },
        { category: 'Security & Risk', title: 'Compliance & SOC2 Analysis', action: () => router.navigate('/app/security/compliance') },
        { category: 'Security & Risk', title: 'TPRM Queue', action: () => router.navigate('/app/security/tprm') }
      );
    }

    // Legal routes
    if (['legal_reviewer', 'tenant_admin'].includes(role)) {
      commands.push(
        { category: 'Legal', title: 'Contract Reviews', action: () => router.navigate('/app/legal/contracts') },
        { category: 'Legal', title: 'Terms & Liability Check', action: () => router.navigate('/app/legal/terms') },
        { category: 'Legal', title: 'Legal Approval Queue', action: () => router.navigate('/app/legal/queue') }
      );
    }

    // Tenant Admin routes
    if (role === 'tenant_admin') {
      commands.push(
        { category: 'Administration', title: 'Organization Settings', action: () => router.navigate('/app/admin/org') },
        { category: 'Administration', title: 'User & Role Management', action: () => router.navigate('/app/admin/users') },
        { category: 'Administration', title: 'Invite Team Members', action: () => router.navigate('/app/admin/invitations') },
        { category: 'Administration', title: 'Audit Chain Log', action: () => router.navigate('/app/admin/audit') },
        { category: 'Administration', title: 'Policy Engine Config', action: () => router.navigate('/app/admin/policies') }
      );
    }

    return commands;
  }

  _filterCommands(query = '') {
    const all = this._getCommands();
    if (!query) {
      this.results = all;
    } else {
      const q = query.toLowerCase();
      this.results = all.filter(c =>
        c.title.toLowerCase().includes(q) || c.category.toLowerCase().includes(q)
      );
    }
    this.activeIndex = 0;
    this._renderResults();
  }

  _renderResults() {
    this.resultsList.innerHTML = '';

    if (this.results.length === 0) {
      const emptyLi = document.createElement('li');
      emptyLi.className = 'cmd-palette-item';
      emptyLi.style.justifyContent = 'center';
      emptyLi.style.color = 'var(--text-muted)';
      emptyLi.textContent = 'No matching commands or destinations';
      this.resultsList.appendChild(emptyLi);
      return;
    }

    let currentCat = '';
    this.results.forEach((item, idx) => {
      if (item.category !== currentCat) {
        currentCat = item.category;
        const groupTitle = document.createElement('li');
        groupTitle.className = 'cmd-palette-group-title';
        groupTitle.textContent = currentCat;
        this.resultsList.appendChild(groupTitle);
      }

      const li = document.createElement('li');
      li.className = `cmd-palette-item ${idx === this.activeIndex ? 'is-active' : ''}`;
      li.setAttribute('role', 'option');
      li.setAttribute('aria-selected', idx === this.activeIndex ? 'true' : 'false');

      li.innerHTML = `
        <span>${item.title}</span>
        <span class="kbd-badge">Select</span>
      `;

      li.addEventListener('click', () => {
        this.close();
        item.action();
      });

      this.resultsList.appendChild(li);
    });
  }

  _handleKeyDown(e) {
    if (!this.isOpen) return;

    if (e.key === 'Escape') {
      e.preventDefault();
      this.close();
      return;
    }

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (this.results.length > 0) {
        this.activeIndex = (this.activeIndex + 1) % this.results.length;
        this._renderResults();
        this._scrollActiveIntoView();
      }
      return;
    }

    if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (this.results.length > 0) {
        this.activeIndex = (this.activeIndex - 1 + this.results.length) % this.results.length;
        this._renderResults();
        this._scrollActiveIntoView();
      }
      return;
    }

    if (e.key === 'Enter') {
      e.preventDefault();
      if (this.results[this.activeIndex]) {
        const item = this.results[this.activeIndex];
        this.close();
        item.action();
      }
    }
  }

  _scrollActiveIntoView() {
    const activeEl = this.resultsList.querySelector('.cmd-palette-item.is-active');
    if (activeEl) {
      activeEl.scrollIntoView({ block: 'nearest' });
    }
  }

  open() {
    this.isOpen = true;
    this.backdrop.classList.add('is-open');
    this.input.value = '';
    this._filterCommands('');
    const doFocus = () => {
      try {
        this.input.focus();
      } catch (_) {}
    };
    doFocus();
    setTimeout(doFocus, 25);
    setTimeout(doFocus, 100);
    requestAnimationFrame(doFocus);
  }

  close() {
    this.isOpen = false;
    this.backdrop.classList.remove('is-open');
  }

  toggle() {
    if (this.isOpen) {
      this.close();
    } else {
      this.open();
    }
  }
}

export const commandPalette = new CommandPalette();
