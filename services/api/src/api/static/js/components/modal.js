/**
 * Omnea Accessible Modal Component (modal.js)
 * Adheres to WAI-ARIA Dialog (Modal) Pattern (WCAG 2.1.1, 2.4.3).
 * Features active focus trap, Escape key dismissal, backdrop dismissal,
 * and restoration of initial trigger focus.
 */

import { escapeHtml } from '../state.js';

class ModalController {
  constructor() {
    this.activeModal = null;
    this.previousActiveElement = null;
    this._handleKeyDown = this._handleKeyDown.bind(this);
  }

  open({ title, bodyContent, footerButtons = [], size = 'md', onDismiss = null }) {
    this.close(); // Close existing modal if open
    this.previousActiveElement = document.activeElement;

    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';
    backdrop.id = 'active-modal-backdrop';

    const sizeClass = size === 'lg' ? 'modal-dialog-lg' : size === 'sm' ? 'modal-dialog-sm' : '';

    const dialog = document.createElement('div');
    dialog.className = `modal-dialog ${sizeClass}`.trim();
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.setAttribute('aria-labelledby', 'modal-dialog-title');

    // Header
    const header = document.createElement('div');
    header.className = 'modal-header';

    const titleEl = document.createElement('h3');
    titleEl.id = 'modal-dialog-title';
    titleEl.className = 'modal-title';
    titleEl.textContent = title || '';

    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'modal-close-btn';
    closeBtn.setAttribute('aria-label', 'Close modal');
    closeBtn.innerHTML = `
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="18" y1="6" x2="6" y2="18"></line>
        <line x1="6" y1="6" x2="18" y2="18"></line>
      </svg>
    `;
    closeBtn.addEventListener('click', () => this.close());

    header.appendChild(titleEl);
    header.appendChild(closeBtn);

    // Body
    const body = document.createElement('div');
    body.className = 'modal-body';
    if (typeof bodyContent === 'string') {
      body.innerHTML = bodyContent;
    } else if (bodyContent instanceof HTMLElement) {
      body.appendChild(bodyContent);
    }

    // Footer
    const footer = document.createElement('div');
    footer.className = 'modal-footer';

    if (footerButtons.length === 0) {
      const defaultCloseBtn = document.createElement('button');
      defaultCloseBtn.type = 'button';
      defaultCloseBtn.className = 'btn btn-secondary';
      defaultCloseBtn.textContent = 'Close';
      defaultCloseBtn.addEventListener('click', () => this.close());
      footer.appendChild(defaultCloseBtn);
    } else {
      footerButtons.forEach(btnConfig => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `btn ${btnConfig.variant ? 'btn-' + btnConfig.variant : 'btn-secondary'}`;
        btn.textContent = btnConfig.text || 'Action';
        if (btnConfig.id) btn.id = btnConfig.id;
        btn.addEventListener('click', (e) => {
          if (btnConfig.onClick) {
            btnConfig.onClick({ close: () => this.close(), event: e });
          } else {
            this.close();
          }
        });
        footer.appendChild(btn);
      });
    }

    dialog.appendChild(header);
    dialog.appendChild(body);
    dialog.appendChild(footer);
    backdrop.appendChild(dialog);

    // Dismiss on backdrop click outside dialog
    backdrop.addEventListener('click', (e) => {
      if (e.target === backdrop) {
        this.close();
      }
    });

    document.body.appendChild(backdrop);
    this.activeModal = { backdrop, dialog, onDismiss };

    // Trigger animation
    requestAnimationFrame(() => {
      backdrop.classList.add('is-open');
      // Trap focus: focus close button or first input
      const focusable = dialog.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
      if (focusable.length > 0) {
        focusable[0].focus();
      }
    });

    document.addEventListener('keydown', this._handleKeyDown);
    return { close: () => this.close() };
  }

  _handleKeyDown(e) {
    if (!this.activeModal) return;

    if (e.key === 'Escape') {
      e.preventDefault();
      this.close();
      return;
    }

    // Tab key focus trapping
    if (e.key === 'Tab') {
      const focusableElements = this.activeModal.dialog.querySelectorAll(
        'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'
      );
      if (focusableElements.length === 0) return;

      const firstElement = focusableElements[0];
      const lastElement = focusableElements[focusableElements.length - 1];

      if (e.shiftKey) {
        if (document.activeElement === firstElement) {
          e.preventDefault();
          lastElement.focus();
        }
      } else {
        if (document.activeElement === lastElement) {
          e.preventDefault();
          firstElement.focus();
        }
      }
    }
  }

  close() {
    if (!this.activeModal) return;

    document.removeEventListener('keydown', this._handleKeyDown);
    const { backdrop, onDismiss } = this.activeModal;
    backdrop.classList.remove('is-open');

    setTimeout(() => {
      if (backdrop.parentElement) {
        backdrop.parentElement.removeChild(backdrop);
      }
      if (typeof onDismiss === 'function') {
        onDismiss();
      }
    }, 200);

    if (this.previousActiveElement && typeof this.previousActiveElement.focus === 'function') {
      this.previousActiveElement.focus();
    }

    this.activeModal = null;
  }
}

export const modal = new ModalController();
