/**
 * Omnea Public Landing Page View (public_home.js)
 * Hero value prop, What It Does, 5-Agent Roster, 3-Step Workflow,
 * Security Architecture Highlights, CTAs, and Theme Toggle.
 */

import { state } from '../state.js';
import { router } from '../router.js';

export function renderPublicHome(container) {
  container.innerHTML = `
    <!-- Public Navigation Bar -->
    <header class="public-nav">
      <a href="#/" class="public-logo">
        <div class="sidebar-logo-icon">Ω</div>
        <span>Omnea</span>
      </a>
      <ul class="public-links">
        <li><a href="#what-it-does" class="public-link">Platform</a></li>
        <li><a href="#agents" class="public-link">Agent Roster</a></li>
        <li><a href="#how-it-works" class="public-link">How It Works</a></li>
        <li><a href="#security" class="public-link">Security Dual-Wall</a></li>
        <li><a href="/docs" target="_blank" class="public-link">Documentation</a></li>
      </ul>
      <div style="display: flex; align-items: center; gap: var(--space-3);">
        <button type="button" class="theme-toggle-btn" id="public-theme-btn" aria-label="Toggle light or dark theme">
          <svg class="sun-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="5"></circle>
            <line x1="12" y1="1" x2="12" y2="3"></line>
            <line x1="12" y1="21" x2="12" y2="23"></line>
            <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line>
            <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line>
            <line x1="1" y1="12" x2="3" y2="12"></line>
            <line x1="21" y1="12" x2="23" y2="12"></line>
            <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line>
            <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line>
          </svg>
        </button>
        <a href="#/login" class="btn btn-secondary btn-sm" id="nav-login-btn">Sign In</a>
        <a href="#/signup" class="btn btn-primary btn-sm" id="nav-signup-btn">Start Procurement</a>
      </div>
    </header>

    <!-- Hero Section -->
    <section class="hero-section">
      <div class="hero-tag">
        <span class="badge-dot"></span>
        AI-Native Autonomous Orchestration
      </div>
      <h1 class="hero-title">
        AI-Native Procurement Orchestration Platform
      </h1>
      <p class="hero-subtitle">
        Eliminate weeks of sequential vendor reviews. Omnea deploys parallel multi-agent evaluation,
        zero-prep context resolution, and cryptographic system-of-record sync with zero demo shortcuts.
      </p>
      <div class="hero-cta-group">
        <a href="#/signup" class="btn btn-primary btn-lg" id="hero-cta-start">Start Procurement</a>
        <a href="#/login" class="btn btn-secondary btn-lg" id="hero-cta-login">Sign In</a>
        <a href="/docs" target="_blank" class="btn btn-ghost btn-lg" id="hero-cta-docs">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"></path>
            <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path>
          </svg>
          View Documentation
        </a>
      </div>
    </section>

    <!-- What It Does Section -->
    <section class="features-section" id="what-it-does">
      <div class="section-header">
        <h2 class="section-title">What It Does</h2>
        <p class="section-subtitle">
          Engineered from the ground up for high-velocity enterprises needing rigorous compliance without administrative drag.
        </p>
      </div>
      <div class="features-grid">
        <div class="feature-card">
          <div class="feature-icon-wrapper">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
            </svg>
          </div>
          <h3 class="feature-card-title">Decentralized Intake</h3>
          <p class="feature-card-desc">
            Employees submit software and service requests naturally via Slack, Microsoft Teams, email, or web UI.
            Omnea automatically parses intent, vendor domain, and scope without requiring dense ERP forms.
          </p>
        </div>

        <div class="feature-card">
          <div class="feature-icon-wrapper">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
            </svg>
          </div>
          <h3 class="feature-card-title">Parallel Multi-Agent Approval Loops</h3>
          <p class="feature-card-desc">
            Instead of routing tickets sequentially through Legal, InfoSec, and Finance over months,
            autonomous specialized agents inspect security baselines, contract terms, and spend caps concurrently in seconds.
          </p>
        </div>

        <div class="feature-card">
          <div class="feature-icon-wrapper">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="2" y1="12" x2="22" y2="12"></line>
              <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path>
            </svg>
          </div>
          <h3 class="feature-card-title">Zero-Prep Context Engine</h3>
          <p class="feature-card-desc">
            Discovers existing vendor relationships, duplicate licenses, past contract amendments, and SOC2 reports
            instantly via Neo4j entity resolution and Milvus vector semantic embeddings.
          </p>
        </div>
      </div>
    </section>

    <!-- Agent Roster Section -->
    <section class="features-section" id="agents" style="background-color: var(--bg-primary);">
      <div class="section-header">
        <h2 class="section-title">Specialized Autonomous Agent Roster</h2>
        <p class="section-subtitle">
          Dedicated, deterministic AI agents operating concurrently under strict human-in-the-loop policies.
        </p>
      </div>
      <div class="roster-grid">
        <div class="agent-card">
          <div class="agent-card-header">
            <div class="agent-avatar agent-classifier">IC</div>
            <div>
              <div class="agent-name">Intake Intent Classifier</div>
              <div class="agent-domain">Natural Language & Categorization</div>
            </div>
          </div>
          <p class="agent-desc">
            Extracts procurement scope, identifies vendor domains, classifies spend taxonomy, and tags initial risk levels.
          </p>
        </div>

        <div class="agent-card">
          <div class="agent-card-header">
            <div class="agent-avatar agent-legal">LR</div>
            <div>
              <div class="agent-name">Legal Reviewer</div>
              <div class="agent-domain">Contracts & Liability</div>
            </div>
          </div>
          <p class="agent-desc">
            Analyzes MSA redlines, indemnification thresholds, limitation of liability, governing law, and DPA agreements.
          </p>
        </div>

        <div class="agent-card">
          <div class="agent-card-header">
            <div class="agent-avatar agent-security">SR</div>
            <div>
              <div class="agent-name">Security Reviewer</div>
              <div class="agent-domain">InfoSec & Compliance</div>
            </div>
          </div>
          <p class="agent-desc">
            Evaluates SOC2 Type II trust criteria, ISO 27001 certifications, encryption at rest/transit, and data residency.
          </p>
        </div>

        <div class="agent-card">
          <div class="agent-card-header">
            <div class="agent-avatar agent-finance">FA</div>
            <div>
              <div class="agent-name">Finance Admin</div>
              <div class="agent-domain">Budgets & Spend Caps</div>
            </div>
          </div>
          <p class="agent-desc">
            Validates department cost centers, enforces strict spend caps, models amortized renewals, and syncs POs.
          </p>
        </div>

        <div class="agent-card">
          <div class="agent-card-header">
            <div class="agent-avatar agent-tprm">TP</div>
            <div>
              <div class="agent-name">TPRM Risk Evaluator</div>
              <div class="agent-domain">Third-Party Risk & Sanctions</div>
            </div>
          </div>
          <p class="agent-desc">
            Scans global sanctions databases, corporate registrations, dark web breach monitors, and adverse media.
          </p>
        </div>
      </div>
    </section>

    <!-- 3-Step How It Works -->
    <section class="features-section" id="how-it-works">
      <div class="section-header">
        <h2 class="section-title">How It Works in 3 Steps</h2>
        <p class="section-subtitle">
          From conversational intake to financial system of record in minutes.
        </p>
      </div>
      <div class="steps-grid">
        <div class="step-card">
          <div class="step-number">1</div>
          <h3 class="step-title">Request via Chat or Web</h3>
          <p class="step-desc">
            Requester initiates procurement via Slack, Teams, email, or web portal with natural language description.
            Omnea gathers all required context dynamically.
          </p>
        </div>

        <div class="step-card">
          <div class="step-number">2</div>
          <h3 class="step-title">Parallel Multi-Agent & Policy Analysis</h3>
          <p class="step-desc">
            Legal, Security, and Finance agents run parallel evaluations against organizational policies,
            budget ceilings, and vendor entity graph connections.
          </p>
        </div>

        <div class="step-card">
          <div class="step-number">3</div>
          <h3 class="step-title">Deterministic System-of-Record Sync</h3>
          <p class="step-desc">
            Upon human approval or automated green-channel clearance, PO is generated and synced to NetSuite,
            SAP, or Coupa with a permanent SHA-256 audit hash.
          </p>
        </div>
      </div>
    </section>

    <!-- Security Highlights Section -->
    <section class="features-section" id="security" style="background-color: var(--bg-primary);">
      <div class="section-header">
        <h2 class="section-title">Enterprise Security Dual-Wall Architecture</h2>
        <p class="section-subtitle">
          Zero demo modes. Built to withstand adversarial penetration and compliance audits.
        </p>
      </div>
      <div class="security-grid">
        <div class="security-card">
          <svg class="security-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
            <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
          </svg>
          <div>
            <h3 style="font-size: var(--text-base); font-weight: var(--font-bold); margin-bottom: 4px;">Fail-Closed PostgreSQL RLS</h3>
            <p style="font-size: var(--text-xs); color: var(--text-secondary); line-height: var(--leading-normal);">
              Database row-level security dual wall guarantees that cross-tenant queries are rejected at the database engine level.
            </p>
          </div>
        </div>

        <div class="security-card">
          <svg class="security-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline>
          </svg>
          <div>
            <h3 style="font-size: var(--text-base); font-weight: var(--font-bold); margin-bottom: 4px;">SHA-256 Audit Chain</h3>
            <p style="font-size: var(--text-xs); color: var(--text-secondary); line-height: var(--leading-normal);">
              Cryptographic hash chain links every agent prompt, decision payload, and human sign-off into an immutable audit trail.
            </p>
          </div>
        </div>

        <div class="security-card">
          <svg class="security-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polygon points="12 2 2 7 12 12 22 7 12 2"></polygon>
            <polyline points="2 17 12 22 22 17"></polyline>
            <polyline points="2 12 12 17 22 12"></polyline>
          </svg>
          <div>
            <h3 style="font-size: var(--text-base); font-weight: var(--font-bold); margin-bottom: 4px;">Strict Spend Caps</h3>
            <p style="font-size: var(--text-xs); color: var(--text-secondary); line-height: var(--leading-normal);">
              Deterministic financial limits and circuit breakers prevent autonomous agents from exceeding approved department budgets.
            </p>
          </div>
        </div>

        <div class="security-card">
          <svg class="security-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
          </svg>
          <div>
            <h3 style="font-size: var(--text-base); font-weight: var(--font-bold); margin-bottom: 4px;">Zero Demo Modes</h3>
            <p style="font-size: var(--text-xs); color: var(--text-secondary); line-height: var(--leading-normal);">
              No hardcoded mock bypasses. Every environment enforces real cryptographic tokens, CSRF protection, and RBAC.
            </p>
          </div>
        </div>
      </div>
    </section>

    <!-- Public Footer -->
    <footer class="public-footer">
      <div class="footer-container">
        <div style="display: flex; align-items: center; gap: var(--space-2);">
          <div class="sidebar-logo-icon" style="width: 24px; height: 24px; font-size: 12px;">Ω</div>
          <span style="font-weight: var(--font-bold); color: var(--text-primary);">Omnea Procurement Orchestration</span>
        </div>
        <div class="footer-copy">
          &copy; 2026 Omnea Inc. All rights reserved. Strict WCAG AA & SOC2 Type II Certified.
        </div>
        <ul class="footer-links">
          <li><a href="/docs" target="_blank" class="public-link">Documentation</a></li>
          <li><a href="#/login" class="public-link">Sign In</a></li>
          <li><a href="#/signup" class="public-link">Create Tenant</a></li>
        </ul>
      </div>
    </footer>
  `;

  // Attach event handlers without inline JS
  const themeBtn = container.querySelector('#public-theme-btn');
  if (themeBtn) {
    themeBtn.addEventListener('click', () => state.toggleTheme());
  }

  // Smooth scroll links
  container.querySelectorAll('a[href^="#"]').forEach(anchor => {
    const href = anchor.getAttribute('href');
    if (href && href.startsWith('#') && !href.startsWith('#/')) {
      anchor.addEventListener('click', (e) => {
        e.preventDefault();
        const targetId = href.substring(1);
        const targetEl = document.getElementById(targetId);
        if (targetEl) {
          targetEl.scrollIntoView({ behavior: 'smooth' });
        }
      });
    }
  });
}
