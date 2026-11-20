/**
 * Omnea Authentication Views (auth_views.js)
 * Signup, Login, Forgot Password, Reset Password, and Accept Invite Views.
 * Strict client-side validation, zero inline scripts, and CSRF protection.
 */

import { authApi } from '../api.js';
import { state, escapeHtml } from '../state.js';
import { router } from '../router.js';
import { toast } from '../components/toast.js';

export function renderSignup(container) {
  container.innerHTML = `
    <div class="auth-wrapper">
      <div class="auth-card">
        <div class="auth-header">
          <div class="auth-logo-badge">Ω</div>
          <h2 class="auth-title">Create Tenant Organization</h2>
          <p class="auth-subtitle">Provision your enterprise workspace and become tenant admin.</p>
        </div>

        <form id="signup-form" novalidate>
          <div class="form-group">
            <label class="form-label form-label-required" for="org-name">Organization Name</label>
            <input type="text" id="org-name" class="form-input" placeholder="e.g. Acme Corporation" required minlength="2">
            <div class="form-error" id="org-name-error" style="display: none;"></div>
          </div>

          <div class="form-group">
            <label class="form-label form-label-required" for="admin-name">Admin Full Name</label>
            <input type="text" id="admin-name" class="form-input" placeholder="e.g. Jane Doe" required>
            <div class="form-error" id="admin-name-error" style="display: none;"></div>
          </div>

          <div class="form-group">
            <label class="form-label form-label-required" for="email">Work Email</label>
            <input type="email" id="email" class="form-input" placeholder="jane@acme.com" required>
            <div class="form-error" id="email-error" style="display: none;"></div>
          </div>

          <div class="form-group">
            <label class="form-label form-label-required" for="password">Password</label>
            <input type="password" id="password" class="form-input" placeholder="••••••••••••" required minlength="8">
            <div class="form-help">Must be at least 8 characters with letters, numbers, and symbols.</div>
            <div class="form-error" id="password-error" style="display: none;"></div>
          </div>

          <button type="submit" class="btn btn-primary btn-lg" style="width: 100%; margin-top: var(--space-4);" id="signup-submit-btn">
            Provision Organization
          </button>
        </form>

        <div class="auth-footer">
          Already registered? <a href="#/login">Sign in to your account</a>
          <br><br>
          <a href="/docs" target="_blank">View Platform Documentation</a>
        </div>
      </div>
    </div>
  `;

  const form = container.querySelector('#signup-form');
  const submitBtn = container.querySelector('#signup-submit-btn');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const orgName = container.querySelector('#org-name').value.trim();
    const fullName = container.querySelector('#admin-name').value.trim();
    const email = container.querySelector('#email').value.trim();
    const password = container.querySelector('#password').value;

    let hasError = false;

    if (!orgName || orgName.length < 2) {
      _showFieldError(container, '#org-name-error', 'Organization name must be at least 2 characters');
      hasError = true;
    } else {
      _hideFieldError(container, '#org-name-error');
    }

    if (!fullName) {
      _showFieldError(container, '#admin-name-error', 'Admin full name is required');
      hasError = true;
    } else {
      _hideFieldError(container, '#admin-name-error');
    }

    if (!email || !email.includes('@')) {
      _showFieldError(container, '#email-error', 'Valid work email is required');
      hasError = true;
    } else {
      _hideFieldError(container, '#email-error');
    }

    if (!password || password.length < 8) {
      _showFieldError(container, '#password-error', 'Password must be at least 8 characters');
      hasError = true;
    } else {
      _hideFieldError(container, '#password-error');
    }

    if (hasError) return;

    submitBtn.classList.add('btn-loading');

    try {
      const res = await authApi.signup({
        organizationName: orgName,
        fullName,
        email,
        password
      });

      toast.success('Organization created! You can now sign in.');
      setTimeout(() => {
        router.navigate('/login');
      }, 1000);
    } catch (err) {
      toast.error(err.message || 'Failed to create organization');
    } finally {
      submitBtn.classList.remove('btn-loading');
    }
  });
}

export function renderLogin(container) {
  container.innerHTML = `
    <div class="auth-wrapper">
      <div class="auth-card">
        <div class="auth-header">
          <div class="auth-logo-badge">Ω</div>
          <h2 class="auth-title">Sign In to Omnea</h2>
          <p class="auth-subtitle">Enter your credentials to access the orchestration console.</p>
        </div>

        <form id="login-form" novalidate>
          <div class="form-group">
            <label class="form-label form-label-required" for="login-email">Email</label>
            <input type="email" id="login-email" class="form-input" placeholder="user@domain.com" required>
            <div class="form-error" id="login-email-error" style="display: none;"></div>
          </div>

          <div class="form-group">
            <label class="form-label form-label-required" for="login-password">Password</label>
            <input type="password" id="login-password" class="form-input" placeholder="••••••••••••" required>
            <div class="form-error" id="login-password-error" style="display: none;"></div>
            <div style="display: flex; justify-content: flex-end; margin-top: var(--space-2);">
              <a href="#/forgot-password" class="btn-link" style="font-size: var(--text-xs);">Forgot password?</a>
            </div>
          </div>

          <div class="form-group" id="totp-group" style="display: none;">
            <label class="form-label" for="login-totp">Two-Factor Authenticator Code (TOTP)</label>
            <input type="text" id="login-totp" class="form-input" placeholder="6-digit code" maxlength="8">
          </div>

          <button type="submit" class="btn btn-primary btn-lg" style="width: 100%; margin-top: var(--space-4);" id="login-submit-btn">
            Sign In
          </button>
        </form>

        <div class="auth-footer">
          Don't have a tenant yet? <a href="#/signup">Start Organization Setup</a>
          <br><br>
          <a href="/docs" target="_blank">Platform Documentation</a>
        </div>
      </div>
    </div>
  `;

  const form = container.querySelector('#login-form');
  const submitBtn = container.querySelector('#login-submit-btn');
  const totpGroup = container.querySelector('#totp-group');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = container.querySelector('#login-email').value.trim();
    const password = container.querySelector('#login-password').value;
    const totpCode = container.querySelector('#login-totp').value.trim();

    let hasError = false;
    if (!email || !email.includes('@')) {
      _showFieldError(container, '#login-email-error', 'Valid email is required');
      hasError = true;
    } else {
      _hideFieldError(container, '#login-email-error');
    }

    if (!password) {
      _showFieldError(container, '#login-password-error', 'Password is required');
      hasError = true;
    } else {
      _hideFieldError(container, '#login-password-error');
    }

    if (hasError) return;

    submitBtn.classList.add('btn-loading');

    try {
      const res = await authApi.login({ email, password, totpCode });

      if (res.totp_required && !totpCode) {
        totpGroup.style.display = 'block';
        toast.info('Two-Factor Authentication required. Please enter your TOTP code.');
        container.querySelector('#login-totp').focus();
        return;
      }

      // Store auth session
      const userObj = {
        userId: res.user_id || email,
        email: email,
        fullName: res.user_id || email
      };
      const roles = res.roles && res.roles.length > 0 ? res.roles : ['requester'];

      state.setSession(
        res.access_token,
        res.csrf_token || '',
        userObj,
        res.tenant_id || 'default',
        roles,
        res.refresh_token || ''
      );

      if (window.procureDesktop?.saveAuthTokens) {
        try {
          await window.procureDesktop.saveAuthTokens({
            accessToken: res.access_token,
            refreshToken: res.refresh_token || '',
            tenantId: res.tenant_id || 'default',
            email: email
          });
        } catch (desktopErr) {
          console.warn('Desktop saveAuthTokens error in auth view:', desktopErr);
        }
      }

      toast.success('Signed in successfully!');
      router.navigate('/app');
    } catch (err) {
      toast.error(err.message || 'Invalid credentials or tenant mismatch');
    } finally {
      submitBtn.classList.remove('btn-loading');
    }
  });
}

export function renderForgotPassword(container) {
  container.innerHTML = `
    <div class="auth-wrapper">
      <div class="auth-card">
        <div class="auth-header">
          <div class="auth-logo-badge">Ω</div>
          <h2 class="auth-title">Reset Your Password</h2>
          <p class="auth-subtitle">Enter your email and we'll send a cryptographic reset token.</p>
        </div>

        <form id="forgot-form" novalidate>
          <div class="form-group">
            <label class="form-label form-label-required" for="forgot-email">Account Email</label>
            <input type="email" id="forgot-email" class="form-input" placeholder="user@domain.com" required>
            <div class="form-error" id="forgot-email-error" style="display: none;"></div>
          </div>

          <button type="submit" class="btn btn-primary btn-lg" style="width: 100%; margin-top: var(--space-4);" id="forgot-submit-btn">
            Send Reset Instructions
          </button>
        </form>

        <div class="auth-footer">
          Remember your password? <a href="#/login">Return to Sign In</a>
          <br><br>
          <a href="/docs" target="_blank">Documentation</a>
        </div>
      </div>
    </div>
  `;

  const form = container.querySelector('#forgot-form');
  const submitBtn = container.querySelector('#forgot-submit-btn');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = container.querySelector('#forgot-email').value.trim();
    if (!email || !email.includes('@')) {
      _showFieldError(container, '#forgot-email-error', 'Valid email is required');
      return;
    }

    submitBtn.classList.add('btn-loading');

    try {
      await authApi.forgotPassword(email);
      toast.success('If the account exists, a reset token has been issued.');
      setTimeout(() => {
        router.navigate('/reset-password');
      }, 1500);
    } catch (err) {
      toast.error(err.message || 'Error requesting password reset');
    } finally {
      submitBtn.classList.remove('btn-loading');
    }
  });
}

export function renderResetPassword(container, params = {}) {
  const defaultToken = params.token || '';

  container.innerHTML = `
    <div class="auth-wrapper">
      <div class="auth-card">
        <div class="auth-header">
          <div class="auth-logo-badge">Ω</div>
          <h2 class="auth-title">Set New Password</h2>
          <p class="auth-subtitle">Enter your reset token and choose a secure password.</p>
        </div>

        <form id="reset-form" novalidate>
          <div class="form-group">
            <label class="form-label form-label-required" for="reset-token">Reset Token</label>
            <input type="text" id="reset-token" class="form-input" value="${escapeHtml(defaultToken)}" placeholder="Paste reset token here" required>
            <div class="form-error" id="reset-token-error" style="display: none;"></div>
          </div>

          <div class="form-group">
            <label class="form-label form-label-required" for="reset-new-password">New Password</label>
            <input type="password" id="reset-new-password" class="form-input" placeholder="••••••••••••" required minlength="8">
            <div class="form-help">Must be at least 8 characters with letters, numbers, and symbols.</div>
            <div class="form-error" id="reset-password-error" style="display: none;"></div>
          </div>

          <button type="submit" class="btn btn-primary btn-lg" style="width: 100%; margin-top: var(--space-4);" id="reset-submit-btn">
            Update Password
          </button>
        </form>

        <div class="auth-footer">
          <a href="#/login">Return to Sign In</a>
          <br><br>
          <a href="/docs" target="_blank">Documentation</a>
        </div>
      </div>
    </div>
  `;

  const form = container.querySelector('#reset-form');
  const submitBtn = container.querySelector('#reset-submit-btn');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const token = container.querySelector('#reset-token').value.trim();
    const newPassword = container.querySelector('#reset-new-password').value;

    if (!token) {
      _showFieldError(container, '#reset-token-error', 'Reset token is required');
      return;
    }
    if (!newPassword || newPassword.length < 8) {
      _showFieldError(container, '#reset-password-error', 'Password must be at least 8 characters');
      return;
    }

    submitBtn.classList.add('btn-loading');

    try {
      await authApi.resetPassword(token, newPassword);
      toast.success('Password updated successfully! Please sign in.');
      setTimeout(() => {
        router.navigate('/login');
      }, 1000);
    } catch (err) {
      toast.error(err.message || 'Failed to reset password');
    } finally {
      submitBtn.classList.remove('btn-loading');
    }
  });
}

export function renderAcceptInvite(container, params = {}) {
  const defaultToken = params.token || '';

  container.innerHTML = `
    <div class="auth-wrapper">
      <div class="auth-card">
        <div class="auth-header">
          <div class="auth-logo-badge">Ω</div>
          <h2 class="auth-title">Join Tenant Organization</h2>
          <p class="auth-subtitle">Accept invitation to activate your procurement seat.</p>
        </div>

        <form id="invite-form" novalidate>
          <div class="form-group">
            <label class="form-label form-label-required" for="invite-token">Invitation Token</label>
            <input type="text" id="invite-token" class="form-input" value="${escapeHtml(defaultToken)}" placeholder="Paste invitation token" required>
            <div class="form-error" id="invite-token-error" style="display: none;"></div>
          </div>

          <div class="form-group">
            <label class="form-label form-label-required" for="invite-name">Full Name</label>
            <input type="text" id="invite-name" class="form-input" placeholder="Your Full Name" required>
            <div class="form-error" id="invite-name-error" style="display: none;"></div>
          </div>

          <div class="form-group">
            <label class="form-label form-label-required" for="invite-password">Set Password</label>
            <input type="password" id="invite-password" class="form-input" placeholder="••••••••••••" required minlength="8">
            <div class="form-help">Must be at least 8 characters with letters, numbers, and symbols.</div>
            <div class="form-error" id="invite-password-error" style="display: none;"></div>
          </div>

          <button type="submit" class="btn btn-primary btn-lg" style="width: 100%; margin-top: var(--space-4);" id="invite-submit-btn">
            Accept Invitation & Join
          </button>
        </form>

        <div class="auth-footer">
          Already active? <a href="#/login">Sign in</a>
          <br><br>
          <a href="/docs" target="_blank">Documentation</a>
        </div>
      </div>
    </div>
  `;

  const form = container.querySelector('#invite-form');
  const submitBtn = container.querySelector('#invite-submit-btn');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const token = container.querySelector('#invite-token').value.trim();
    const fullName = container.querySelector('#invite-name').value.trim();
    const password = container.querySelector('#invite-password').value;

    if (!token) {
      _showFieldError(container, '#invite-token-error', 'Token is required');
      return;
    }
    if (!fullName) {
      _showFieldError(container, '#invite-name-error', 'Full name is required');
      return;
    }
    if (!password || password.length < 8) {
      _showFieldError(container, '#invite-password-error', 'Password must be at least 8 characters');
      return;
    }

    submitBtn.classList.add('btn-loading');

    try {
      await authApi.acceptInvite(token, password, fullName);
      toast.success('Invitation accepted! Please sign in with your new password.');
      setTimeout(() => {
        router.navigate('/login');
      }, 1000);
    } catch (err) {
      toast.error(err.message || 'Invalid or expired invitation token');
    } finally {
      submitBtn.classList.remove('btn-loading');
    }
  });
}

function _showFieldError(container, selector, msg) {
  const el = container.querySelector(selector);
  if (el) {
    el.textContent = msg;
    el.style.display = 'block';
  }
}

function _hideFieldError(container, selector) {
  const el = container.querySelector(selector);
  if (el) {
    el.textContent = '';
    el.style.display = 'none';
  }
}
