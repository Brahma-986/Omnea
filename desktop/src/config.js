'use strict';

const fs = require('fs');
const path = require('path');
const os = require('os');
const http = require('http');
const https = require('https');

let electronApp = null;
try {
  const electron = require('electron');
  if (typeof electron === 'object' && electron !== null) {
    electronApp = electron.app || (electron.remote && electron.remote.app);
  }
} catch {
  // Running in Node / test environment without electron runtime
}

function getUserDataPath() {
  if (process.env.OMNEA_USER_DATA) {
    return process.env.OMNEA_USER_DATA;
  }
  if (electronApp && typeof electronApp.getPath === 'function') {
    try {
      return electronApp.getPath('userData');
    } catch {
      // Fallback if app is not yet ready
    }
  }
  return path.join(os.homedir(), '.omnea-procure');
}

function getConfigPath() {
  return path.join(getUserDataPath(), 'config.json');
}

function getDefaultConfig() {
  return {
    serverUrl: 'http://127.0.0.1:8000',
    pollIntervalMs: 30000,
    offlineMode: false,
    autoCheckUpdates: true,
    launchOnStartup: false,
    theme: 'system',
  };
}

function validateServerUrl(rawUrl) {
  if (!rawUrl || typeof rawUrl !== 'string') {
    throw new Error('Server URL must be a non-empty string');
  }
  const trimmed = rawUrl.trim();
  const parsed = new URL(trimmed);
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new Error('Server URL must use http:// or https:// protocol');
  }
  // Remove trailing slashes for consistency
  return `${parsed.protocol}//${parsed.host}${parsed.pathname === '/' ? '' : parsed.pathname}`.replace(/\/+$/, '');
}

function loadConfig() {
  const configPath = getConfigPath();
  const defaults = getDefaultConfig();
  try {
    if (fs.existsSync(configPath)) {
      const data = fs.readFileSync(configPath, 'utf8');
      const parsed = JSON.parse(data);
      return { ...defaults, ...parsed };
    }
  } catch (err) {
    console.error(`[Config] Failed to read config from ${configPath}:`, err.message);
  }
  return defaults;
}

function saveConfig(updates = {}) {
  const current = loadConfig();
  const newConfig = { ...current };

  if (updates.serverUrl !== undefined) {
    newConfig.serverUrl = validateServerUrl(updates.serverUrl);
  }
  if (updates.pollIntervalMs !== undefined) {
    const interval = Number(updates.pollIntervalMs);
    if (isNaN(interval) || interval < 5000) {
      throw new Error('Poll interval must be at least 5000 ms');
    }
    newConfig.pollIntervalMs = interval;
  }
  if (updates.offlineMode !== undefined) {
    newConfig.offlineMode = Boolean(updates.offlineMode);
  }
  if (updates.autoCheckUpdates !== undefined) {
    newConfig.autoCheckUpdates = Boolean(updates.autoCheckUpdates);
  }
  if (updates.launchOnStartup !== undefined) {
    newConfig.launchOnStartup = Boolean(updates.launchOnStartup);
  }
  if (updates.theme !== undefined) {
    newConfig.theme = updates.theme;
  }

  const dir = path.dirname(getConfigPath());
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }

  fs.writeFileSync(getConfigPath(), JSON.stringify(newConfig, null, 2), 'utf8');
  return newConfig;
}

function testConnection(targetUrl, timeoutMs = 5000) {
  return new Promise((resolve) => {
    let cleanUrl;
    try {
      cleanUrl = validateServerUrl(targetUrl);
    } catch (err) {
      return resolve({
        ok: false,
        status: 0,
        latencyMs: 0,
        url: targetUrl,
        error: `Invalid URL: ${err.message}`,
      });
    }

    const testEndpoint = `${cleanUrl}/health`;
    const urlObj = new URL(testEndpoint);
    const client = urlObj.protocol === 'https:' ? https : http;
    const startTime = Date.now();

    const req = client.request(
      urlObj,
      {
        method: 'GET',
        timeout: timeoutMs,
        headers: {
          'Accept': 'application/json',
          'User-Agent': 'OmneaProcureOS-Desktop/1.0.0',
        },
      },
      (res) => {
        const latencyMs = Date.now() - startTime;
        const ok = res.statusCode >= 200 && res.statusCode < 400;
        res.resume();
        resolve({
          ok,
          success: ok,
          status: res.statusCode,
          latencyMs,
          url: cleanUrl,
          endpoint: testEndpoint,
        });
      }
    );

    req.on('timeout', () => {
      req.destroy();
      resolve({
        ok: false,
        success: false,
        status: 408,
        latencyMs: Date.now() - startTime,
        url: cleanUrl,
        error: `Connection timed out after ${timeoutMs}ms`,
      });
    });

    req.on('error', (err) => {
      resolve({
        ok: false,
        success: false,
        status: 0,
        latencyMs: Date.now() - startTime,
        url: cleanUrl,
        error: err.message,
      });
    });

    req.end();
  });
}

function setupConfigIpc(ipcMain) {
  if (!ipcMain) return;

  ipcMain.handle('config:get', async () => {
    return loadConfig();
  });

  ipcMain.handle('config:set', async (event, updates) => {
    return saveConfig(updates);
  });

  ipcMain.handle('config:test-connection', async (event, targetUrl) => {
    const config = loadConfig();
    const urlToTest = targetUrl || config.serverUrl;
    return await testConnection(urlToTest);
  });
}

module.exports = {
  getUserDataPath,
  getConfigPath,
  getDefaultConfig,
  validateServerUrl,
  loadConfig,
  saveConfig,
  testConnection,
  setupConfigIpc,
};
