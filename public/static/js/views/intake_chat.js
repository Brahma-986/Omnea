/**
 * Omnea Procurement Intake Chat View (intake_chat.js)
 * Route: #/app/requests/new
 * Multi-turn conversational procurement intake with LLM degradation guard,
 * agent progression stepper, verbatim evidence quote verification,
 * and single-turn clarification resolution.
 */

import { state, escapeHtml } from '../state.js';
import { requestsApi } from '../api.js';
import { toast } from '../components/toast.js';

export async function renderIntakeChat(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h1 class="page-title">New Procurement Request</h1>
        <p class="page-description">Autonomous intake assistant with multi-agent classification, entity resolution, and context assembly.</p>
      </div>
      <div>
        <a href="#/app/requests" class="btn btn-secondary">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="19" y1="12" x2="5" y2="12"></line>
            <polyline points="12 19 5 12 12 5"></polyline>
          </svg>
          Back to My Requests
        </a>
      </div>
    </div>
    <div id="intake-main-content">
      <div class="skeleton" style="height: 350px; border-radius: var(--radius-lg);"></div>
    </div>
  `;

  const mainContent = container.querySelector('#intake-main-content');

  // 1. Check LLM Connectivity
  let llmStatus = { connected: false };
  try {
    llmStatus = await requestsApi.getLlmStatus();
  } catch (err) {
    console.warn('Failed to fetch LLM status:', err);
    llmStatus = { connected: false, error: err.message };
  }

  // 2. Safe Degradation: If not connected, render step-by-step setup guide and disable input
  if (!llmStatus || !llmStatus.connected) {
    _renderNotConnectedCard(mainContent, llmStatus);
    return;
  }

  // 3. Render Live Interactive Chat Interface
  _renderChatInterface(mainContent, llmStatus);
}

/**
 * Render "Not Connected" safe degradation card with setup guide.
 * Strictly avoids fake data paths!
 */
function _renderNotConnectedCard(container, statusInfo) {
  container.innerHTML = `
    <div class="not-connected-card table-card" style="padding: var(--space-6); max-width: 800px; margin: 0 auto;">
      <div style="display: flex; align-items: flex-start; gap: var(--space-4);">
        <div style="width: 48px; height: 48px; border-radius: var(--radius-full); background-color: var(--danger-bg); display: flex; align-items: center; justify-content: center; color: var(--danger); flex-shrink: 0;">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"></circle>
            <line x1="12" y1="8" x2="12" y2="12"></line>
            <line x1="12" y1="16" x2="12.01" y2="16"></line>
          </svg>
        </div>
        <div style="flex: 1;">
          <div style="display: flex; align-items: center; gap: var(--space-2); margin-bottom: var(--space-2);">
            <h2 style="font-size: var(--text-lg); font-weight: var(--font-bold); color: var(--text-primary); margin: 0;">
              LLM Provider Not Connected
            </h2>
            <span class="badge badge-danger">Offline / Disabled</span>
          </div>
          <p style="color: var(--text-secondary); font-size: var(--text-sm); line-height: var(--leading-normal); margin-bottom: var(--space-4);">
            Autonomous procurement intake relies on an active LLM provider connection for structured extraction and multi-agent intent classification. The system has safely halted intake to prevent ungrounded or synthetic allocations.
          </p>

          <div style="background-color: var(--bg-subtle); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); padding: var(--space-4); margin-bottom: var(--space-6);">
            <h3 style="font-size: var(--text-sm); font-weight: var(--font-semibold); margin-bottom: var(--space-2); color: var(--text-primary);">
              Step-by-Step Configuration Guide
            </h3>
            <ol style="margin: 0; padding-left: var(--space-4); font-size: var(--text-sm); color: var(--text-secondary); display: flex; flex-direction: column; gap: var(--space-2);">
              <li>Open the environment file <code>.env</code> in the repository root.</li>
              <li>Provide a valid API key: <code>LLM_API_KEY=&lt;your_key&gt;</code> (or <code>GEMINI_API_KEY</code>).</li>
              <li>Optionally configure provider & model: <code>LLM_PROVIDER=gemini</code> and <code>LLM_MODEL=gemini-3.1-flash-lite</code>.</li>
              <li>Restart the Omnea API service or refresh this page once set.</li>
            </ol>
          </div>

          <div class="chat-input-disabled-box" style="opacity: 0.6; pointer-events: none; background: var(--bg-subtle); border: 1px dashed var(--border-strong); border-radius: var(--radius-md); padding: var(--space-4); display: flex; gap: var(--space-3); align-items: center;">
            <input type="text" class="form-input" disabled placeholder="Intake is disabled: LLM_API_KEY is not configured" style="flex: 1; cursor: not-allowed;">
            <button type="button" class="btn btn-secondary" disabled style="cursor: not-allowed;">Send</button>
          </div>
        </div>
      </div>
    </div>
  `;
}

/**
 * Render the interactive intake chat with visual stepper and evidence cards.
 */
function _renderChatInterface(container, statusInfo) {
  let sessionId = null;
  let originalMessageText = '';
  let attachedFile = null;
  let pendingClarification = false;

  container.innerHTML = `
    <!-- Agent Progression Stepper -->
    <div class="agent-stepper" id="intake-stepper" aria-label="Intake Agent Progression">
      <div class="stepper-step" data-step="0">
        <div class="stepper-step-indicator">
          <span class="step-num">1</span>
          <svg class="step-check" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12"></polyline>
          </svg>
        </div>
        <div class="stepper-step-content">
          <span class="stepper-step-label">Intake Agent</span>
          <span class="stepper-step-sub">User Intent</span>
        </div>
      </div>
      <div class="stepper-line"></div>

      <div class="stepper-step" data-step="1">
        <div class="stepper-step-indicator">
          <span class="step-num">2</span>
          <svg class="step-check" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12"></polyline>
          </svg>
        </div>
        <div class="stepper-step-content">
          <span class="stepper-step-label">Classifier</span>
          <span class="stepper-step-sub">Spend & Quotes</span>
        </div>
      </div>
      <div class="stepper-line"></div>

      <div class="stepper-step" data-step="2">
        <div class="stepper-step-indicator">
          <span class="step-num">3</span>
          <svg class="step-check" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12"></polyline>
          </svg>
        </div>
        <div class="stepper-step-content">
          <span class="stepper-step-label">Entity-Resolution</span>
          <span class="stepper-step-sub">Vendor Master</span>
        </div>
      </div>
      <div class="stepper-line"></div>

      <div class="stepper-step" data-step="3">
        <div class="stepper-step-indicator">
          <span class="step-num">4</span>
          <svg class="step-check" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12"></polyline>
          </svg>
        </div>
        <div class="stepper-step-content">
          <span class="stepper-step-label">Context Assembler</span>
          <span class="stepper-step-sub">Budgets & Citations</span>
        </div>
      </div>
    </div>

    <!-- Chat Stream Container -->
    <div class="chat-container">
      <div class="chat-messages" id="chat-messages-list">
        <!-- Welcoming Agent Message -->
        <div class="chat-message chat-agent">
          <div class="chat-avatar">Ω</div>
          <div class="chat-bubble">
            <p style="margin: 0 0 var(--space-2) 0;">
              Hello! I am your AI Procurement Intake Assistant.
            </p>
            <p style="margin: 0; color: var(--text-secondary); font-size: var(--text-sm);">
              Enter your purchase requirement in plain English (e.g. <em>"Datadog, 48k USD/year for observability"</em>) and optionally attach a vendor quote PDF.
            </p>
          </div>
        </div>
      </div>

      <!-- Attached File Chip Container -->
      <div id="chat-attachment-preview" style="display: none; padding: var(--space-2) var(--space-4); background-color: var(--bg-subtle); border-top: 1px solid var(--border-subtle);"></div>

      <!-- Input Bar -->
      <form class="chat-input-bar" id="intake-chat-form" novalidate>
        <input type="file" id="quote-file-input" accept=".pdf,.docx,.doc,.txt" style="display: none;">
        <button type="button" class="btn btn-ghost" id="attach-file-btn" title="Attach Quote PDF" aria-label="Attach vendor quote PDF">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path>
          </svg>
        </button>

        <textarea
          id="chat-input-field"
          class="chat-input-textarea"
          placeholder="Describe your purchase requirement (e.g. Datadog, 48k USD/year)..."
          rows="1"
          required
        ></textarea>

        <button type="submit" class="btn btn-primary" id="chat-send-btn" aria-label="Send message">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="22" y1="2" x2="11" y2="13"></line>
            <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
          </svg>
        </button>
      </form>
    </div>
  `;

  const messagesList = container.querySelector('#chat-messages-list');
  const chatForm = container.querySelector('#intake-chat-form');
  const inputField = container.querySelector('#chat-input-field');
  const sendBtn = container.querySelector('#chat-send-btn');
  const fileInput = container.querySelector('#quote-file-input');
  const attachBtn = container.querySelector('#attach-file-btn');
  const attachmentPreview = container.querySelector('#chat-attachment-preview');

  // File Attachment Handlers
  attachBtn.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) {
      attachedFile = file;
      attachmentPreview.style.display = 'flex';
      attachmentPreview.innerHTML = `
        <div class="chat-attachment-pill">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
            <polyline points="14 2 14 8 20 8"></polyline>
          </svg>
          <span style="font-weight: var(--font-medium);">${escapeHtml(file.name)}</span>
          <span style="color: var(--text-muted); font-size: var(--text-xs);">(${Math.round(file.size / 1024)} KB)</span>
          <button type="button" class="pill-remove-btn" aria-label="Remove attachment">&times;</button>
        </div>
      `;
      attachmentPreview.querySelector('.pill-remove-btn').addEventListener('click', () => {
        attachedFile = null;
        fileInput.value = '';
        attachmentPreview.style.display = 'none';
        attachmentPreview.innerHTML = '';
      });
    }
  });

  // Enter to send (Shift+Enter for newline)
  inputField.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      chatForm.dispatchEvent(new Event('submit'));
    }
  });

  // Stepper helper
  function updateStepper(stepIndex, isCompleted = false) {
    const steps = container.querySelectorAll('.stepper-step');
    steps.forEach((step, idx) => {
      step.classList.remove('active', 'completed');
      if (idx < stepIndex || isCompleted) {
        step.classList.add('completed');
      } else if (idx === stepIndex) {
        step.classList.add('active');
      }
    });
  }

  // Submit Handler
  chatForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = inputField.value.trim();
    if (!text && !attachedFile) return;

    // Reset input
    inputField.value = '';
    sendBtn.classList.add('btn-loading');
    sendBtn.disabled = true;

    // Append User Message to Thread
    const userMsgEl = document.createElement('div');
    userMsgEl.className = 'chat-message chat-user';
    let fileHtml = '';
    if (attachedFile) {
      fileHtml = `
        <div class="chat-attachment-pill" style="margin-bottom: var(--space-2); background: rgba(255,255,255,0.2); border-color: rgba(255,255,255,0.3); color: #fff;">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path></svg>
          <span>${escapeHtml(attachedFile.name)}</span>
        </div>
      `;
    }
    userMsgEl.innerHTML = `
      <div class="chat-bubble">
        ${fileHtml}
        <div>${escapeHtml(text)}</div>
      </div>
    `;
    messagesList.appendChild(userMsgEl);
    messagesList.scrollTop = messagesList.scrollHeight;

    // Save initial text
    if (!originalMessageText) {
      originalMessageText = text;
    }

    // Step 0: Intake Agent Active
    updateStepper(0);

    // Live Agent Progression Animation
    setTimeout(() => updateStepper(1), 300);
    setTimeout(() => updateStepper(2), 600);

    try {
      const payload = {
        message: text,
        session_id: sessionId,
        attachments: attachedFile ? [{ filename: attachedFile.name, size: attachedFile.size }] : []
      };

      const res = await requestsApi.postIntakeChat(payload);
      if (res.session_id) sessionId = res.session_id;

      // Handle Clarification Needed
      if (res.status === 'clarification_needed') {
        updateStepper(1); // Classifier identified missing fields
        _renderClarificationNeeded(messagesList, res, (clarificationReply) => {
          // Re-submit merged reply
          _handleClarificationReply(clarificationReply);
        });
      } else if (res.status === 'created') {
        // Step 4: Context Assembler Complete -> All finished
        updateStepper(3, true);
        _renderCreatedSuccess(messagesList, res);
      } else {
        // Generic response
        _renderGenericAgentResponse(messagesList, res);
      }
    } catch (err) {
      toast.error(err.message || 'Intake failed. Please retry.');
      const errMsg = document.createElement('div');
      errMsg.className = 'chat-message chat-agent';
      errMsg.innerHTML = `
        <div class="chat-avatar">Ω</div>
        <div class="chat-bubble" style="border-left: 3px solid var(--danger);">
          <p style="color: var(--danger); font-weight: var(--font-semibold); margin: 0;">Error processing intake:</p>
          <p style="margin: 4px 0 0 0; font-size: var(--text-sm);">${escapeHtml(err.message)}</p>
        </div>
      `;
      messagesList.appendChild(errMsg);
    } finally {
      sendBtn.classList.remove('btn-loading');
      sendBtn.disabled = false;
      attachedFile = null;
      fileInput.value = '';
      attachmentPreview.style.display = 'none';
      attachmentPreview.innerHTML = '';
      messagesList.scrollTop = messagesList.scrollHeight;
    }
  });

  // Handle Clarification In-Thread Reply
  async function _handleClarificationReply(replyText) {
    if (!replyText || !replyText.trim()) return;

    // Append User Clarification to Thread
    const userMsg = document.createElement('div');
    userMsg.className = 'chat-message chat-user';
    userMsg.innerHTML = `<div class="chat-bubble">${escapeHtml(replyText)}</div>`;
    messagesList.appendChild(userMsg);
    messagesList.scrollTop = messagesList.scrollHeight;

    // Stepper re-trigger
    updateStepper(1);
    setTimeout(() => updateStepper(2), 250);
    setTimeout(() => updateStepper(3), 500);

    // Merge original text with clarification
    const mergedText = `${originalMessageText}\n\nClarification reply:\n${replyText.trim()}`;
    originalMessageText = mergedText;

    try {
      const res = await requestsApi.postIntakeChat({
        message: mergedText,
        session_id: sessionId
      });

      if (res.session_id) sessionId = res.session_id;

      if (res.status === 'clarification_needed') {
        updateStepper(1);
        _renderClarificationNeeded(messagesList, res, (newReply) => {
          _handleClarificationReply(newReply);
        });
      } else if (res.status === 'created') {
        updateStepper(3, true);
        _renderCreatedSuccess(messagesList, res);
      } else {
        _renderGenericAgentResponse(messagesList, res);
      }
    } catch (err) {
      toast.error(err.message || 'Failed to submit clarification.');
    } finally {
      messagesList.scrollTop = messagesList.scrollHeight;
    }
  }
}

/**
 * Render Extracted Fields with Evidence Quotes Card
 */
function _buildExtractedFieldsHtml(extractedFields, evidence = {}) {
  if (!extractedFields || Object.keys(extractedFields).length === 0) return '';

  const vendor = extractedFields.vendor_name || extractedFields.vendor || null;
  const amount = extractedFields.raw_amount_str || extractedFields.estimated_spend || null;
  const currency = extractedFields.currency || extractedFields.raw_currency_str || 'USD';
  const period = extractedFields.raw_billing_period_str || extractedFields.billing_period || 'Annual';
  const justification = extractedFields.justification || null;

  return `
    <div class="quote-evidence-card" style="margin-top: var(--space-3);">
      <div class="quote-card-header">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="20 6 9 17 4 12"></polyline>
        </svg>
        <span>Extracted Intent & Evidence Quotes</span>
      </div>
      <div class="quote-fields-grid">
        ${vendor ? `
          <div class="quote-field-row">
            <span class="quote-field-label">Vendor / Tool:</span>
            <span class="quote-field-value">${escapeHtml(vendor)}</span>
            ${evidence.vendor_name ? `
              <div class="quote-snippet">
                <span class="quote-badge">Quote</span> "${escapeHtml(evidence.vendor_name)}"
              </div>
            ` : ''}
          </div>
        ` : ''}

        ${amount ? `
          <div class="quote-field-row">
            <span class="quote-field-label">Estimated Spend:</span>
            <span class="quote-field-value">${escapeHtml(amount)} ${escapeHtml(currency)} (${escapeHtml(period)})</span>
            ${evidence.raw_amount_str ? `
              <div class="quote-snippet">
                <span class="quote-badge">Quote</span> "${escapeHtml(evidence.raw_amount_str)}"
              </div>
            ` : ''}
          </div>
        ` : ''}

        ${justification ? `
          <div class="quote-field-row">
            <span class="quote-field-label">Business Justification:</span>
            <span class="quote-field-value">${escapeHtml(justification)}</span>
            ${evidence.justification ? `
              <div class="quote-snippet">
                <span class="quote-badge">Quote</span> "${escapeHtml(evidence.justification)}"
              </div>
            ` : ''}
          </div>
        ` : ''}
      </div>
    </div>
  `;
}

/**
 * Render Clarification Needed Agent Card with In-Thread Reply Box
 */
function _renderClarificationNeeded(messagesList, res, onReplyCallback) {
  const card = document.createElement('div');
  card.className = 'chat-message chat-agent';

  const question = res.clarifying_question || 'Could you please clarify the missing budget and vendor details?';
  const missing = res.missing_fields || [];
  const fieldsHtml = _buildExtractedFieldsHtml(res.extracted_fields, res.evidence);

  card.innerHTML = `
    <div class="chat-avatar">Ω</div>
    <div class="chat-bubble">
      <div style="font-weight: var(--font-semibold); color: var(--warning); margin-bottom: var(--space-2); display: flex; align-items: center; gap: var(--space-1);">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10"></circle>
          <line x1="12" y1="8" x2="12" y2="12"></line>
          <line x1="12" y1="16" x2="12.01" y2="16"></line>
        </svg>
        Clarification Required
      </div>

      <p style="margin: 0 0 var(--space-3) 0; font-size: var(--text-base); color: var(--text-primary); font-weight: var(--font-medium);">
        ${escapeHtml(question)}
      </p>

      ${missing.length > 0 ? `
        <div style="font-size: var(--text-xs); color: var(--text-muted); margin-bottom: var(--space-3);">
          Missing mandatory fields: <span style="font-weight: var(--font-semibold); color: var(--danger);">${escapeHtml(missing.join(', '))}</span>
        </div>
      ` : ''}

      ${fieldsHtml}

      <!-- In-Thread Reply Input Form -->
      <form class="in-thread-reply-form" style="margin-top: var(--space-4); display: flex; gap: var(--space-2);">
        <input
          type="text"
          class="form-input in-thread-input"
          placeholder="Reply to clarifying question (e.g. $48,000 USD/year)..."
          required
          style="flex: 1;"
        >
        <button type="submit" class="btn btn-primary in-thread-send-btn">
          Send Reply
        </button>
      </form>
    </div>
  `;

  const form = card.querySelector('.in-thread-reply-form');
  const input = card.querySelector('.in-thread-input');
  const btn = card.querySelector('.in-thread-send-btn');

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const val = input.value.trim();
    if (!val) return;
    input.disabled = true;
    btn.disabled = true;
    onReplyCallback(val);
  });

  messagesList.appendChild(card);
}

/**
 * Render Success Card when status == "created"
 */
function _renderCreatedSuccess(messagesList, res) {
  const card = document.createElement('div');
  card.className = 'chat-message chat-agent';

  const reqId = res.request_id || 'REQ-' + Date.now().toString(36);
  const fieldsHtml = _buildExtractedFieldsHtml(res.extracted_fields, res.evidence);

  card.innerHTML = `
    <div class="chat-avatar">Ω</div>
    <div class="chat-bubble" style="border-left: 4px solid var(--success);">
      <div style="display: flex; align-items: center; gap: var(--space-2); margin-bottom: var(--space-2);">
        <div style="width: 24px; height: 24px; border-radius: 50%; background: var(--success); color: #fff; display: flex; align-items: center; justify-content: center;">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
            <polyline points="20 6 9 17 4 12"></polyline>
          </svg>
        </div>
        <h3 style="font-size: var(--text-base); font-weight: var(--font-bold); color: var(--success); margin: 0;">
          Procurement Request Created Successfully
        </h3>
      </div>

      <p style="margin: 0 0 var(--space-3) 0; font-size: var(--text-sm); color: var(--text-secondary);">
        Your request has been dispatched to autonomous reviewer agents (Finance, Security, Legal) and queued for approval.
      </p>

      <div style="background-color: var(--bg-subtle); padding: var(--space-3); border-radius: var(--radius-md); margin-bottom: var(--space-4); display: flex; justify-content: space-between; align-items: center;">
        <div>
          <span style="font-size: var(--text-xs); color: var(--text-muted); text-transform: uppercase;">Request Identifier:</span>
          <div style="font-family: var(--font-mono); font-weight: var(--font-bold); font-size: var(--text-sm); color: var(--brand-primary);">${escapeHtml(reqId)}</div>
        </div>
        <span class="badge badge-compliant">Status: In Review</span>
      </div>

      ${fieldsHtml}

      <div style="display: flex; gap: var(--space-3); margin-top: var(--space-4);">
        <a href="#/app/requests/${escapeHtml(reqId)}" class="btn btn-primary" id="btn-view-created-request">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
            <circle cx="12" cy="12" r="3"></circle>
          </svg>
          View Request Details
        </a>
        <a href="#/app/requests" class="btn btn-secondary">
          All Requests
        </a>
      </div>
    </div>
  `;

  messagesList.appendChild(card);
}

/**
 * Render Generic Agent Response
 */
function _renderGenericAgentResponse(messagesList, res) {
  const card = document.createElement('div');
  card.className = 'chat-message chat-agent';
  const fieldsHtml = _buildExtractedFieldsHtml(res.extracted_fields, res.evidence);

  card.innerHTML = `
    <div class="chat-avatar">Ω</div>
    <div class="chat-bubble">
      <p style="margin: 0 0 var(--space-2) 0;">${escapeHtml(res.message || 'Request information received.')}</p>
      ${fieldsHtml}
    </div>
  `;

  messagesList.appendChild(card);
}
