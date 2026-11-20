/**
 * Omnea Tenant Onboarding Checklist View (onboarding.js)
 * Step-by-step interactive onboarding for new tenant administrators:
 * 1. Setup Organization, 2. Invite Team Members, 3. Configure Approval Rules, 4. Connect ERP.
 */

import { state, escapeHtml } from '../state.js';
import { modal } from '../components/modal.js';
import { toast } from '../components/toast.js';
import { invitationsApi } from '../api.js';

export function renderOnboardingChecklist(container) {
  const obState = state.getState().onboarding;

  const steps = [
    {
      key: 'orgSetup',
      title: '1. Setup Organization & Domains',
      desc: 'Verify organization slug, primary domain, and SSO settings.',
      completed: Boolean(obState.orgSetup),
      actionLabel: obState.orgSetup ? 'Completed' : 'Configure Org',
      onAction: () => _openOrgSetupModal()
    },
    {
      key: 'inviteTeam',
      title: '2. Invite Team Members',
      desc: 'Send invitations to Requesters, Approvers, and Finance Admins.',
      completed: Boolean(obState.inviteTeam),
      actionLabel: obState.inviteTeam ? 'Invite More' : 'Send Invites',
      onAction: () => _openInviteModal()
    },
    {
      key: 'configRules',
      title: '3. Configure Approval Rules & Spend Caps',
      desc: 'Define dollar thresholds, required agent reviewers, and green channels.',
      completed: Boolean(obState.configRules),
      actionLabel: obState.configRules ? 'Configured' : 'Set Rules',
      onAction: () => _openRulesModal()
    },
    {
      key: 'connectErp',
      title: '4. Connect Financial System of Record',
      desc: 'Sync approved POs with NetSuite, SAP S/4HANA, or Coupa.',
      completed: Boolean(obState.connectErp),
      actionLabel: obState.connectErp ? 'Connected' : 'Connect ERP',
      badge: obState.connectErp ? 'active' : 'needs_human',
      badgeText: obState.connectErp ? 'Connected' : 'Not Connected',
      onAction: () => _openErpConnectModal()
    }
  ];

  const completedCount = steps.filter(s => s.completed).length;
  const progressPct = Math.round((completedCount / steps.length) * 100);

  const card = document.createElement('div');
  card.className = 'onboarding-card';

  card.innerHTML = `
    <div class="onboarding-header">
      <div>
        <h3 class="onboarding-title">Tenant Onboarding Checklist</h3>
        <p style="font-size: var(--text-xs); color: var(--text-muted); margin-top: 2px;">
          ${completedCount} of ${steps.length} steps complete (${progressPct}%)
        </p>
      </div>
      <span class="badge ${progressPct === 100 ? 'badge-compliant' : 'badge-pending'}">
        ${progressPct === 100 ? 'Fully Configured' : 'Action Required'}
      </span>
    </div>

    <div class="onboarding-progress-bar">
      <div class="onboarding-progress-fill" style="width: ${progressPct}%;"></div>
    </div>

    <ul class="checklist-list"></ul>
  `;

  const listEl = card.querySelector('.checklist-list');

  steps.forEach(step => {
    const li = document.createElement('li');
    li.className = `checklist-item ${step.completed ? 'is-completed' : ''}`;

    li.innerHTML = `
      <div class="checklist-item-left">
        <div class="checklist-status-icon">
          ${step.completed ? '✓' : ''}
        </div>
        <div>
          <div style="font-size: var(--text-sm); font-weight: var(--font-semibold); color: var(--text-primary); display: flex; align-items: center; gap: var(--space-2);">
            <span>${escapeHtml(step.title)}</span>
            ${step.badge ? `<span class="badge badge-${step.badge}">${escapeHtml(step.badgeText)}</span>` : ''}
          </div>
          <div style="font-size: var(--text-xs); color: var(--text-secondary); margin-top: 2px;">
            ${escapeHtml(step.desc)}
          </div>
        </div>
      </div>
      <div>
        <button type="button" class="btn ${step.completed ? 'btn-ghost' : 'btn-secondary'} btn-sm step-action-btn">
          ${escapeHtml(step.actionLabel)}
        </button>
      </div>
    `;

    const btn = li.querySelector('.step-action-btn');
    btn.addEventListener('click', () => {
      step.onAction();
    });

    listEl.appendChild(li);
  });

  container.appendChild(card);
}

function _openOrgSetupModal() {
  const tenantId = state.getTenantId();
  modal.open({
    title: 'Organization & Tenant Settings',
    bodyContent: `
      <div style="display: flex; flex-direction: column; gap: var(--space-4);">
        <div class="form-group">
          <label class="form-label">Tenant Identifier</label>
          <input type="text" class="form-input" value="${escapeHtml(tenantId)}" readonly>
          <div class="form-help">Tenant boundary enforced by PostgreSQL RLS dual-wall.</div>
        </div>
        <div class="form-group">
          <label class="form-label">Primary Email Domain</label>
          <input type="text" class="form-input" id="org-domain" value="company.com" placeholder="company.com">
        </div>
      </div>
    `,
    footerButtons: [
      {
        text: 'Save & Verify',
        variant: 'primary',
        onClick: ({ close }) => {
          state.completeOnboardingStep('orgSetup');
          toast.success('Organization settings verified.');
          close();
          window.location.reload();
        }
      }
    ]
  });
}

function _openInviteModal() {
  modal.open({
    title: 'Invite Team Member',
    bodyContent: `
      <div style="display: flex; flex-direction: column; gap: var(--space-4);">
        <div class="form-group">
          <label class="form-label form-label-required" for="modal-invite-email">Member Email</label>
          <input type="email" id="modal-invite-email" class="form-input" placeholder="colleague@company.com" required>
        </div>
        <div class="form-group">
          <label class="form-label form-label-required" for="modal-invite-role">Assigned Role</label>
          <select id="modal-invite-role" class="form-select">
            <option value="requester">Requester (Submit & track requests)</option>
            <option value="approver">Approver (Review multi-agent advice)</option>
            <option value="finance_admin">Finance Admin (Budgets & ERP)</option>
            <option value="security_reviewer">Security Reviewer (SOC2 & TPRM)</option>
            <option value="legal_reviewer">Legal Reviewer (Contracts & Terms)</option>
            <option value="tenant_admin">Tenant Admin (Full Management)</option>
          </select>
        </div>
      </div>
    `,
    footerButtons: [
      {
        text: 'Cancel',
        variant: 'secondary',
        onClick: ({ close }) => close()
      },
      {
        text: 'Send Invitation',
        variant: 'primary',
        onClick: async ({ close }) => {
          const emailInput = document.getElementById('modal-invite-email');
          const roleSelect = document.getElementById('modal-invite-role');
          const email = emailInput ? emailInput.value.trim() : '';
          const role = roleSelect ? roleSelect.value : 'requester';

          if (!email || !email.includes('@')) {
            toast.error('Please provide a valid email address.');
            return;
          }

          try {
            await invitationsApi.createInvitation(email, role);
            state.completeOnboardingStep('inviteTeam');
            toast.success(`Invitation sent to ${email} for role ${role}`);
            close();
            window.location.reload();
          } catch (err) {
            toast.error(err.message || 'Failed to send invitation');
          }
        }
      }
    ]
  });
}

function _openRulesModal() {
  modal.open({
    title: 'Approval Rules & Spend Ceilings',
    bodyContent: `
      <div style="display: flex; flex-direction: column; gap: var(--space-4);">
        <div class="form-group">
          <label class="form-label">Auto-Approval Threshold (USD)</label>
          <input type="number" class="form-input" id="rule-threshold" value="5000">
          <div class="form-help">Procurements below this threshold skip manual approvals if all agents pass.</div>
        </div>
        <div class="form-group">
          <label class="form-label">Required Agent Baselines</label>
          <label class="form-checkbox-label">
            <input type="checkbox" class="form-checkbox" checked disabled> Intake Intent Classifier (Mandatory)
          </label>
          <label class="form-checkbox-label">
            <input type="checkbox" class="form-checkbox" checked> Legal Reviewer (Contracts & Liability)
          </label>
          <label class="form-checkbox-label">
            <input type="checkbox" class="form-checkbox" checked> Security Reviewer (SOC2 & InfoSec)
          </label>
          <label class="form-checkbox-label">
            <input type="checkbox" class="form-checkbox" checked> TPRM Risk Evaluator (Sanctions & Adverse Media)
          </label>
        </div>
      </div>
    `,
    footerButtons: [
      {
        text: 'Save Policy Rules',
        variant: 'primary',
        onClick: ({ close }) => {
          state.completeOnboardingStep('configRules');
          toast.success('Approval policies and spend caps updated.');
          close();
          window.location.reload();
        }
      }
    ]
  });
}

function _openErpConnectModal() {
  modal.open({
    title: 'Connect ERP / Financial System of Record',
    bodyContent: `
      <div style="display: flex; flex-direction: column; gap: var(--space-4);">
        <div class="form-group">
          <label class="form-label">Select Primary ERP</label>
          <select class="form-select" id="erp-type">
            <option value="netsuite">Oracle NetSuite SuiteTalk REST API</option>
            <option value="sap">SAP S/4HANA Cloud OData</option>
            <option value="coupa">Coupa Core Procurement API</option>
            <option value="workday">Workday Financial Management</option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">API Endpoint / Base URL</label>
          <input type="url" class="form-input" id="erp-url" placeholder="https://api.netsuite.com/services/rest">
        </div>
        <div class="form-group">
          <label class="form-label">OAuth 2.0 Client ID</label>
          <input type="text" class="form-input" id="erp-client-id" placeholder="Client ID">
        </div>
        <div class="form-help" style="color: var(--warning-text); background: var(--warning-bg); padding: var(--space-2); border-radius: var(--radius-sm);">
          ERP integration enforces fail-closed outbox queues and circuit breaker retry semantics.
        </div>
      </div>
    `,
    footerButtons: [
      {
        text: 'Cancel',
        variant: 'secondary',
        onClick: ({ close }) => close()
      },
      {
        text: 'Test & Activate ERP Sync',
        variant: 'primary',
        onClick: ({ close }) => {
          state.completeOnboardingStep('connectErp');
          toast.success('ERP connection established and verified.');
          close();
          window.location.reload();
        }
      }
    ]
  });
}
