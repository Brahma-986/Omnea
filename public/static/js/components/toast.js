/**
 * Omnea Toast Notification Component (toast.js)
 * Accessible floating live region (WCAG 4.1.3 Status Messages)
 * Supports programmatic dismissal, progress timer, and clean DOM creation.
 */

import { escapeHtml } from '../state.js';

class ToastManager {
  constructor() {
    this.container = null;
    this._initContainer();
  }

  _initContainer() {
    if (typeof document === 'undefined') return;
    let el = document.getElementById('toast-root');
    if (!el) {
      el = document.createElement('div');
      el.id = 'toast-root';
      el.className = 'toast-container';
      el.setAttribute('aria-live', 'polite');
      el.setAttribute('aria-atomic', 'false');
      document.body.appendChild(el);
    }
    this.container = el;
  }

  show({ title, message, type = 'info', duration = 4500 }) {
    if (!this.container) this._initContainer();

    const toastEl = document.createElement('div');
    toastEl.className = `toast toast-${type}`;
    toastEl.setAttribute('role', type === 'error' ? 'alert' : 'status');

    // Accessible SVGs per type
    let iconSvg = '';
    if (type === 'success') {
      iconSvg = '<svg class="toast-icon" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/></svg>';
    } else if (type === 'error') {
      iconSvg = '<svg class="toast-icon" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"/></svg>';
    } else if (type === 'warning') {
      iconSvg = '<svg class="toast-icon" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/></svg>';
    } else {
      iconSvg = '<svg class="toast-icon" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clip-rule="evenodd"/></svg>';
    }

    // Build DOM structure safely
    toastEl.innerHTML = `
      ${iconSvg}
      <div class="toast-content">
        ${title ? `<div class="toast-title">${escapeHtml(title)}</div>` : ''}
        <div class="toast-message">${escapeHtml(message)}</div>
      </div>
      <button type="button" class="toast-close" aria-label="Dismiss notification">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="18" y1="6" x2="6" y2="18"></line>
          <line x1="6" y1="6" x2="18" y2="18"></line>
        </svg>
      </button>
      <div class="toast-progress"></div>
    `;

    const closeBtn = toastEl.querySelector('.toast-close');
    const progressBar = toastEl.querySelector('.toast-progress');

    let dismissed = false;
    const dismiss = () => {
      if (dismissed) return;
      dismissed = true;
      toastEl.classList.add('toast-hiding');
      setTimeout(() => {
        if (toastEl.parentElement) {
          toastEl.parentElement.removeChild(toastEl);
        }
      }, 250);
    };

    closeBtn.addEventListener('click', dismiss);

    if (duration > 0) {
      progressBar.style.transition = `transform ${duration}ms linear`;
      requestAnimationFrame(() => {
        progressBar.style.transform = 'scaleX(0)';
      });
      setTimeout(dismiss, duration);
    } else {
      progressBar.style.display = 'none';
    }

    this.container.appendChild(toastEl);
  }

  success(message, title = 'Success') {
    this.show({ title, message, type: 'success' });
  }

  error(message, title = 'Error') {
    this.show({ title, message, type: 'error' });
  }

  warning(message, title = 'Warning') {
    this.show({ title, message, type: 'warning' });
  }

  info(message, title = 'Information') {
    this.show({ title, message, type: 'info' });
  }
}

export const toast = new ToastManager();
