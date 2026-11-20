/**
 * Omnea Layout & UI Primitives (layout.js)
 * Modular layout components, tab controllers, side drawers,
 * visual diff generators, progress bars, and accessible navigation helpers.
 * Fully compatible with light and dark theme design tokens.
 */

import { escapeHtml } from './state.js';

/**
 * Accessible Sub-tab Navigation Component
 * Adheres to WAI-ARIA Tabs pattern (role="tablist", role="tab", aria-selected).
 */
export function createTabs({ container, tabs, activeTabId, onTabChange }) {
  const tabList = document.createElement('div');
  tabList.className = 'tab-list';
  tabList.setAttribute('role', 'tablist');

  const tabButtons = new Map();

  tabs.forEach(tab => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `tab-button tab-btn ${tab.id === activeTabId ? 'is-active' : ''}`;
    btn.setAttribute('role', 'tab');
    btn.setAttribute('aria-selected', tab.id === activeTabId ? 'true' : 'false');
    btn.id = `tab-btn-${tab.id}`;
    btn.setAttribute('data-tab', tab.id);
    btn.setAttribute('aria-controls', `tab-panel-${tab.id}`);

    let badgeHtml = '';
    if (tab.badge !== undefined && tab.badge !== null) {
      badgeHtml = `<span class="badge ${tab.badgeClass || 'badge-neutral'}" style="margin-left: 6px; font-size: 10px;">${escapeHtml(String(tab.badge))}</span>`;
    }

    btn.innerHTML = `
      ${tab.icon ? `<span class="tab-icon" style="margin-right: 6px;">${tab.icon}</span>` : ''}
      <span>${escapeHtml(tab.label)}</span>
      ${badgeHtml}
    `;

    btn.addEventListener('click', () => {
      tabButtons.forEach((b, id) => {
        const isActive = id === tab.id;
        b.classList.toggle('is-active', isActive);
        b.setAttribute('aria-selected', isActive ? 'true' : 'false');
      });
      if (typeof onTabChange === 'function') {
        onTabChange(tab.id);
      }
    });

    tabButtons.set(tab.id, btn);
    tabList.appendChild(btn);
  });

  if (container) {
    container.innerHTML = '';
    container.appendChild(tabList);
  }

  return {
    element: tabList,
    setActiveTab: (id) => {
      tabButtons.forEach((b, tabId) => {
        const isActive = tabId === id;
        b.classList.toggle('is-active', isActive);
        b.setAttribute('aria-selected', isActive ? 'true' : 'false');
      });
    }
  };
}

/**
 * Slide-out Detail Drawer Component
 * Slides in from right for entity inspection (vendor details, contracts, etc.).
 */
export function openDetailDrawer({ id, title, subtitle = '', content, actions = [], onClose = null }) {
  // Dismiss any existing drawer
  closeDetailDrawer();

  const backdrop = document.createElement('div');
  backdrop.className = 'drawer-backdrop';
  backdrop.id = 'active-drawer-backdrop';

  const panel = document.createElement('div');
  panel.className = 'drawer-panel';
  if (id) panel.id = id;
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-modal', 'true');
  panel.setAttribute('aria-labelledby', 'drawer-title');

  // Header
  const header = document.createElement('div');
  header.className = 'drawer-header';
  header.innerHTML = `
    <div>
      <h3 id="drawer-title" class="drawer-title" style="margin: 0; font-size: var(--text-lg); font-weight: var(--font-bold); color: var(--text-primary);">${escapeHtml(title)}</h3>
      ${subtitle ? `<p class="drawer-subtitle" style="margin: 2px 0 0; font-size: var(--text-xs); color: var(--text-muted);">${escapeHtml(subtitle)}</p>` : ''}
    </div>
    <button type="button" class="btn btn-ghost btn-sm close-drawer-btn" aria-label="Close drawer" style="padding: 4px;">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="18" y1="6" x2="6" y2="18"></line>
        <line x1="6" y1="6" x2="18" y2="18"></line>
      </svg>
    </button>
  `;

  // Body
  const body = document.createElement('div');
  body.className = 'drawer-body';
  if (typeof content === 'string') {
    body.innerHTML = content;
  } else if (content instanceof HTMLElement) {
    body.appendChild(content);
  }

  // Footer Actions
  const footer = document.createElement('div');
  footer.className = 'drawer-footer';
  footer.style.display = 'flex';
  footer.style.justifyContent = 'flex-end';
  footer.style.gap = 'var(--space-2)';
  footer.style.padding = 'var(--space-4) var(--space-6)';
  footer.style.borderTop = '1px solid var(--border-subtle)';
  footer.style.backgroundColor = 'var(--bg-surface)';

  if (actions.length === 0) {
    const defaultClose = document.createElement('button');
    defaultClose.type = 'button';
    defaultClose.className = 'btn btn-secondary';
    defaultClose.textContent = 'Close';
    defaultClose.addEventListener('click', () => closeDetailDrawer());
    footer.appendChild(defaultClose);
  } else {
    actions.forEach(action => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `btn ${action.variant ? 'btn-' + action.variant : 'btn-secondary'}`;
      btn.textContent = action.label || 'Action';
      if (action.id) btn.id = action.id;
      btn.addEventListener('click', (e) => {
        if (action.onClick) {
          action.onClick({ close: () => closeDetailDrawer(), event: e });
        } else {
          closeDetailDrawer();
        }
      });
      footer.appendChild(btn);
    });
  }

  panel.appendChild(header);
  panel.appendChild(body);
  panel.appendChild(footer);
  backdrop.appendChild(panel);

  // Close handlers
  const closeBtn = header.querySelector('.close-drawer-btn');
  if (closeBtn) closeBtn.addEventListener('click', () => closeDetailDrawer());

  backdrop.addEventListener('click', (e) => {
    if (e.target === backdrop) {
      closeDetailDrawer();
    }
  });

  const handleEsc = (e) => {
    if (e.key === 'Escape') {
      closeDetailDrawer();
    }
  };
  document.addEventListener('keydown', handleEsc);

  backdrop._escHandler = handleEsc;
  backdrop._onCloseCallback = onClose;

  document.body.appendChild(backdrop);

  // Animate slide-in
  requestAnimationFrame(() => {
    backdrop.classList.add('is-open');
    panel.classList.add('is-open');
  });

  return {
    close: () => closeDetailDrawer(),
    bodyElement: body
  };
}

export function closeDetailDrawer() {
  const backdrop = document.getElementById('active-drawer-backdrop');
  if (!backdrop) return;

  if (backdrop._escHandler) {
    document.removeEventListener('keydown', backdrop._escHandler);
  }

  backdrop.classList.remove('is-open');
  const panel = backdrop.querySelector('.drawer-panel');
  if (panel) panel.classList.remove('is-open');

  setTimeout(() => {
    if (typeof backdrop._onCloseCallback === 'function') {
      backdrop._onCloseCallback();
    }
    if (backdrop.parentNode) {
      backdrop.parentNode.removeChild(backdrop);
    }
  }, 250);
}

/**
 * Metric / KPI Stat Card
 */
export function createStatCard({ title, value, subtext = '', badge = null, iconSvg = '', variant = 'default' }) {
  const card = document.createElement('div');
  card.className = `stat-card stat-card-${variant}`;
  card.innerHTML = `
    <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: var(--space-2);">
      <span class="stat-card-title" style="font-size: var(--text-xs); font-weight: var(--font-medium); color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.05em;">${escapeHtml(title)}</span>
      ${iconSvg ? `<span class="stat-card-icon" style="color: var(--brand-primary); opacity: 0.85;">${iconSvg}</span>` : ''}
    </div>
    <div style="display: flex; align-items: baseline; gap: var(--space-2);">
      <span class="stat-card-value" style="font-size: var(--text-2xl); font-weight: var(--font-bold); color: var(--text-primary); letter-spacing: -0.02em;">${escapeHtml(value)}</span>
      ${badge ? `<span class="badge ${badge.className || 'badge-info'}">${escapeHtml(badge.text)}</span>` : ''}
    </div>
    ${subtext ? `<p class="stat-card-subtext" style="font-size: var(--text-xs); color: var(--text-muted); margin: var(--space-1) 0 0;">${escapeHtml(subtext)}</p>` : ''}
  `;
  return card;
}

/**
 * Visual Progress Bar Component
 */
export function createProgressBar(percentage, status = 'normal') {
  const clamped = Math.max(0, Math.min(100, Math.round(percentage)));
  let fillClass = 'progress-fill-success';
  if (clamped >= 90) {
    fillClass = 'progress-fill-danger';
  } else if (clamped >= 70) {
    fillClass = 'progress-fill-warning';
  }

  const wrapper = document.createElement('div');
  wrapper.className = 'progress-bar-container';
  wrapper.setAttribute('role', 'progressbar');
  wrapper.setAttribute('aria-valuenow', String(clamped));
  wrapper.setAttribute('aria-valuemin', '0');
  wrapper.setAttribute('aria-valuemax', '100');
  wrapper.innerHTML = `
    <div class="progress-bar-fill ${fillClass}" style="width: ${clamped}%;"></div>
  `;
  return wrapper;
}

/**
 * Visual JSON Diff Component
 * Highlights added (+), removed (-), and modified (~) keys between before and after objects.
 */
export function createVisualDiff(beforeObj, afterObj) {
  const container = document.createElement('div');
  container.className = 'diff-viewer';

  const beforeKeys = new Set(Object.keys(beforeObj || {}));
  const afterKeys = new Set(Object.keys(afterObj || {}));
  const allKeys = Array.from(new Set([...beforeKeys, ...afterKeys])).sort();

  const diffLines = [];

  allKeys.forEach(key => {
    const hasBefore = beforeKeys.has(key);
    const hasAfter = afterKeys.has(key);
    const valBefore = beforeObj ? beforeObj[key] : undefined;
    const valAfter = afterObj ? afterObj[key] : undefined;

    const strBefore = JSON.stringify(valBefore, null, 2);
    const strAfter = JSON.stringify(valAfter, null, 2);

    if (!hasBefore && hasAfter) {
      // Added
      diffLines.push({
        type: 'added',
        key,
        content: `+ "${key}": ${strAfter}`
      });
    } else if (hasBefore && !hasAfter) {
      // Removed
      diffLines.push({
        type: 'removed',
        key,
        content: `- "${key}": ${strBefore}`
      });
    } else if (strBefore !== strAfter) {
      // Changed
      diffLines.push({
        type: 'changed',
        key,
        content: `~ "${key}":\n    Before: ${strBefore}\n    After:  ${strAfter}`
      });
    } else {
      // Unchanged
      diffLines.push({
        type: 'unchanged',
        key,
        content: `  "${key}": ${strAfter}`
      });
    }
  });

  const pre = document.createElement('pre');
  pre.className = 'diff-code-block';
  pre.style.margin = '0';
  pre.style.padding = 'var(--space-3)';
  pre.style.overflowX = 'auto';

  diffLines.forEach(line => {
    const div = document.createElement('div');
    div.className = `diff-line diff-line-${line.type}`;
    div.textContent = line.content;
    pre.appendChild(div);
  });

  container.appendChild(pre);
  return container;
}

/**
 * Callout Banner / Alert
 */
export function createAlert({ variant = 'info', title = '', message = '', icon = '' }) {
  const el = document.createElement('div');
  el.className = `alert-banner alert-${variant}`;
  el.style.display = 'flex';
  el.style.alignItems = 'flex-start';
  el.style.gap = 'var(--space-3)';
  el.style.padding = 'var(--space-3) var(--space-4)';
  el.style.borderRadius = 'var(--radius-md)';
  el.style.marginBottom = 'var(--space-4)';

  let bg = 'var(--info-bg)';
  let border = 'var(--info-border)';
  let color = 'var(--info-text)';
  if (variant === 'danger' || variant === 'rejected') {
    bg = 'var(--danger-bg)';
    border = 'var(--danger-border)';
    color = 'var(--danger-text)';
  } else if (variant === 'warning') {
    bg = 'var(--warning-bg)';
    border = 'var(--warning-border)';
    color = 'var(--warning-text)';
  } else if (variant === 'success') {
    bg = 'var(--success-bg)';
    border = 'var(--success-border)';
    color = 'var(--success-text)';
  }

  el.style.backgroundColor = bg;
  el.style.border = `1px solid ${border}`;
  el.style.color = color;

  el.innerHTML = `
    ${icon ? `<span class="alert-icon" style="flex-shrink: 0; margin-top: 2px;">${icon}</span>` : ''}
    <div style="flex: 1;">
      ${title ? `<strong style="display: block; font-size: var(--text-sm); font-weight: var(--font-bold); margin-bottom: 2px;">${escapeHtml(title)}</strong>` : ''}
      <div style="font-size: var(--text-xs); line-height: 1.5;">${escapeHtml(message)}</div>
    </div>
  `;
  return el;
}
