'use strict';

const http = require('http');
const https = require('https');
const path = require('path');
const { loadConfig } = require('./config');
const { getTokens } = require('./keychain');
const { saveCachedRequests } = require('./offline_cache');

let electronNotification = null;
try {
  const electron = require('electron');
  electronNotification = electron.Notification;
} catch {
  // Test / Node environment
}

class ApprovalsMonitor {
  constructor(options = {}) {
    this.getMainWindow = options.getMainWindow || (() => null);
    this.onBadgeUpdate = options.onBadgeUpdate || (() => {});
    this.onNotification = options.onNotification || (() => {});
    this.iconPath = options.iconPath || path.join(__dirname, '../assets/icon.png');
    this.pollIntervalMs = options.pollIntervalMs || 30000;

    this.timer = null;
    this.isRunning = false;
    this.knownStepIds = new Set();
    this.isInitialRun = true;
    this.lastChecked = null;
    this.pendingCount = 0;
  }

  start(intervalMs) {
    if (this.isRunning) {
      this.stop();
    }
    if (intervalMs && intervalMs >= 5000) {
      this.pollIntervalMs = intervalMs;
    }
    this.isRunning = true;
    // Immediate initial poll
    this.poll();
    this.timer = setInterval(() => this.poll(), this.pollIntervalMs);
  }

  stop() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this.isRunning = false;
  }

  async checkNow() {
    return await this.poll();
  }

  async poll() {
    this.lastChecked = new Date().toISOString();
    const tokens = getTokens();
    if (!tokens) {
      return { success: false, reason: 'unauthenticated' };
    }

    const accessToken = typeof tokens === 'string' ? tokens : tokens.access_token;
    if (!accessToken) {
      return { success: false, reason: 'no_access_token' };
    }

    const config = loadConfig();
    const serverUrl = config.serverUrl;

    try {
      const data = await this._fetchPendingApprovals(serverUrl, accessToken);
      const items = Array.isArray(data.pending_approvals) ? data.pending_approvals : [];
      this.pendingCount = items.length;

      // Update offline cache with the fresh requests
      try {
        saveCachedRequests(items);
      } catch (cacheErr) {
        console.warn('[ApprovalsMonitor] Failed to update offline cache:', cacheErr.message);
      }

      // Update Tray badge count
      this.onBadgeUpdate(this.pendingCount);

      // Check for new approvals to notify
      const newItems = items.filter((item) => !this.knownStepIds.has(item.step_id));

      if (this.isInitialRun) {
        // On first startup, seed known IDs and optionally show summary if pending > 0
        items.forEach((item) => this.knownStepIds.add(item.step_id));
        this.isInitialRun = false;

        if (items.length > 0) {
          this._emitNotification({
            title: 'Omnea ProcureOS',
            body: `You have ${items.length} pending approval${items.length > 1 ? 's' : ''} awaiting review.`,
            step: items[0],
            serverUrl,
          });
        }
      } else {
        // Subsequent polls: notify for each newly arrived approval step
        for (const item of newItems) {
          this.knownStepIds.add(item.step_id);
          const spendStr = item.estimated_spend ? ` (${item.currency || '$'}${Number(item.estimated_spend).toLocaleString()})` : '';
          const body = `${item.step_name || 'Approval'} for ${item.vendor_name || 'Request #' + item.request_id}${spendStr}`;

          this._emitNotification({
            title: item.sla_status === 'expired' || item.sla_status === 'escalated'
              ? 'Urgent Approval Required'
              : 'New Approval Assigned',
            body,
            step: item,
            serverUrl,
            urgent: item.sla_status === 'expired' || item.sla_status === 'escalated',
          });
        }
      }

      return { success: true, count: this.pendingCount, items };
    } catch (err) {
      // Don't crash on network failure
      console.warn('[ApprovalsMonitor] Poll error:', err.message);
      return { success: false, error: err.message };
    }
  }

  _fetchPendingApprovals(serverUrl, token) {
    return new Promise((resolve, reject) => {
      const targetUrl = new URL(`${serverUrl.replace(/\/+$/, '')}/api/v1/approvals/pending`);
      const client = targetUrl.protocol === 'https:' ? https : http;

      const req = client.request(
        targetUrl,
        {
          method: 'GET',
          timeout: 10000,
          headers: {
            'Authorization': `Bearer ${token}`,
            'Accept': 'application/json',
            'User-Agent': 'OmneaProcureOS-Desktop/1.0.0',
          },
        },
        (res) => {
          let body = '';
          res.on('data', (chunk) => {
            body += chunk;
          });
          res.on('end', () => {
            if (res.statusCode >= 200 && res.statusCode < 300) {
              try {
                resolve(JSON.parse(body));
              } catch (e) {
                reject(new Error(`Failed to parse approvals JSON: ${e.message}`));
              }
            } else {
              reject(new Error(`HTTP ${res.statusCode}: ${body.slice(0, 100)}`));
            }
          });
        }
      );

      req.on('timeout', () => {
        req.destroy();
        reject(new Error('Approvals check timed out'));
      });

      req.on('error', (err) => {
        reject(err);
      });

      req.end();
    });
  }

  _emitNotification({ title, body, step, serverUrl, urgent }) {
    if (typeof this.onNotification === 'function') {
      try {
        this.onNotification({ title, body, step, serverUrl, urgent });
      } catch (err) {
        console.warn('[ApprovalsMonitor] onNotification callback error:', err.message);
      }
    }

    if (!electronNotification || !electronNotification.isSupported()) {
      return;
    }

    try {
      const notification = new electronNotification({
        title,
        body,
        icon: this.iconPath,
        urgency: urgent ? 'critical' : 'normal',
        silent: false,
      });

      notification.on('click', () => {
        const win = this.getMainWindow();
        if (win) {
          if (win.isMinimized()) win.restore();
          win.show();
          win.focus();

          if (step && step.request_id) {
            const target = `${serverUrl.replace(/\/+$/, '')}/requests/${step.request_id}`;
            win.loadURL(target).catch(() => {});
            win.webContents.send('approval:selected', step);
          }
        }
      });

      notification.show();
    } catch (err) {
      console.warn('[ApprovalsMonitor] Notification display error:', err.message);
    }
  }

  getStatus() {
    return {
      isRunning: this.isRunning,
      pendingCount: this.pendingCount,
      lastChecked: this.lastChecked,
      pollIntervalMs: this.pollIntervalMs,
    };
  }
}

function setupNotificationsIpc(ipcMain, monitor) {
  if (!ipcMain || !monitor) return;

  ipcMain.handle('notifications:check-now', async () => {
    return await monitor.checkNow();
  });

  ipcMain.handle('notifications:get-status', async () => {
    return monitor.getStatus();
  });
}

module.exports = {
  ApprovalsMonitor,
  setupNotificationsIpc,
};
