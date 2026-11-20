'use strict';

const path = require('path');

let electron = {};
try {
  const mod = require('electron');
  if (typeof mod === 'object' && mod !== null) {
    electron = mod;
  }
} catch {
  // Test / Node runner
}

const app = electron.app || {
  requestSingleInstanceLock: () => true,
  setAsDefaultProtocolClient: () => {},
  on: () => {},
  whenReady: () => ({ then: () => {} }),
  quit: () => {},
  getPath: () => '',
  getName: () => 'omnea-procure-desktop',
  getVersion: () => '1.0.0',
};
const BrowserWindow = electron.BrowserWindow || class {};
const ipcMain = electron.ipcMain || { handle: () => {}, on: () => {} };
const shell = electron.shell || { openExternal: () => Promise.resolve() };
const session = electron.session || { defaultSession: { webRequest: { onHeadersReceived: () => {} } } };

const { loadConfig, saveConfig, setupConfigIpc } = require('./config');
const { setupKeychainIpc } = require('./keychain');
const { setupOfflineCacheIpc } = require('./offline_cache');
const { parseDeepLink, findDeepLinkInArgv, buildServerUrlForDeepLink } = require('./deep_link');
const { ApprovalsMonitor, setupNotificationsIpc } = require('./notifications');
const { createTray, destroyTray, getTrayInstance } = require('./tray');
const { initUpdater, setupUpdaterIpc } = require('./updater');

// Single-instance lock
const isRunningInElectron = Boolean(electron.app);
let gotTheLock = true;

if (isRunningInElectron) {
  gotTheLock = app.requestSingleInstanceLock();
  if (!gotTheLock) {
    console.log('[Main] Another instance is already running. Quitting.');
    app.quit();
    process.exit(0);
  }

  // Deep-link protocol registration
  if (process.defaultApp) {
    if (process.argv.length >= 2) {
      app.setAsDefaultProtocolClient('procure', process.execPath, [path.resolve(process.argv[1])]);
    }
  } else {
    app.setAsDefaultProtocolClient('procure');
  }
}

let mainWindow = null;
let approvalsMonitor = null;
let pendingDeepLink = null;

// Check startup command line for deep link
const startupDeepLinkUrl = findDeepLinkInArgv(process.argv);
if (startupDeepLinkUrl) {
  pendingDeepLink = parseDeepLink(startupDeepLinkUrl);
}

// Second-instance event handler
if (isRunningInElectron) {
  app.on('second-instance', (event, commandLine) => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
    }

    const deepLinkUrl = findDeepLinkInArgv(commandLine);
    if (deepLinkUrl) {
      const parsed = parseDeepLink(deepLinkUrl);
      if (parsed) {
        handleDeepLink(parsed);
      }
    }
  });

  // macOS open-url handler
  app.on('open-url', (event, url) => {
    event.preventDefault();
    const parsed = parseDeepLink(url);
    if (parsed) {
      if (mainWindow && mainWindow.webContents) {
        handleDeepLink(parsed);
      } else {
        pendingDeepLink = parsed;
      }
    }
  });
}

function handleDeepLink(deepLink) {
  if (!deepLink) return;
  const config = loadConfig();

  if (mainWindow && !mainWindow.isDestroyed()) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();

    if (deepLink.action === 'settings') {
      loadSettingsView();
      return;
    }

    const targetUrl = buildServerUrlForDeepLink(config.serverUrl, deepLink);
    if (targetUrl) {
      mainWindow.loadURL(targetUrl).catch((err) => {
        console.warn(`[DeepLink] Failed to load deep link URL ${targetUrl}:`, err.message);
        loadOfflineView();
      });
    }

    mainWindow.webContents.send('deeplink:received', deepLink);
  }
}

function applySecurityPolicy(ses, serverUrl) {
  if (!ses || !ses.webRequest) return;

  let serverOrigin = '';
  try {
    const parsed = new URL(serverUrl);
    serverOrigin = parsed.origin;
  } catch {
    serverOrigin = 'http://127.0.0.1:8000';
  }

  ses.webRequest.onHeadersReceived((details, callback) => {
    const csp = [
      "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: " + serverOrigin,
      "script-src 'self' 'unsafe-inline' 'unsafe-eval' " + serverOrigin,
      "style-src 'self' 'unsafe-inline' " + serverOrigin,
      "img-src 'self' data: blob: https: " + serverOrigin,
      "font-src 'self' data: " + serverOrigin,
      "connect-src 'self' " + serverOrigin + " ws: wss:",
      "frame-ancestors 'none'",
    ].join('; ');

    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [csp],
        'X-Content-Type-Options': ['nosniff'],
        'X-Frame-Options': ['DENY'],
      },
    });
  });
}

function isAllowedUrl(navigationUrl, serverUrl) {
  if (!navigationUrl || typeof navigationUrl !== 'string') return false;

  // Internal local views
  if (navigationUrl.startsWith('file://')) {
    return true;
  }

  try {
    const targetObj = new URL(navigationUrl);
    const serverObj = new URL(serverUrl);

    // Permit identical origin (protocol + host + port)
    return targetObj.origin === serverObj.origin;
  } catch {
    return false;
  }
}

function createMainWindow() {
  const config = loadConfig();
  const iconPath = path.join(__dirname, '../assets/icon.png');

  mainWindow = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 1024,
    minHeight: 700,
    title: 'Omnea ProcureOS',
    icon: iconPath,
    backgroundColor: '#f8fafc',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  // Apply strict CSP
  applySecurityPolicy(session.defaultSession, config.serverUrl);

  // Navigation allow-list: block unauthorized origins
  mainWindow.webContents.on('will-navigate', (event, navigationUrl) => {
    const currentConfig = loadConfig();
    if (!isAllowedUrl(navigationUrl, currentConfig.serverUrl)) {
      event.preventDefault();
      console.warn(`[Security] Blocked unauthorized navigation to: ${navigationUrl}`);
      if (navigationUrl.startsWith('http://') || navigationUrl.startsWith('https://')) {
        shell.openExternal(navigationUrl);
      }
    }
  });

  // Block popups and window.open, delegating external links to OS browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    console.log(`[WindowOpenHandler] Opening external URL: ${url}`);
    if (url.startsWith('http://') || url.startsWith('https://')) {
      shell.openExternal(url);
    }
    return { action: 'deny' };
  });

  // Fallback to offline view on connection failure
  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription, validatedURL) => {
    if (errorCode === -3) return;

    console.warn(`[Main] Failed to load ${validatedURL} (${errorCode}: ${errorDescription}). Loading offline view.`);
    loadOfflineView();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  setupIpcHandlers();
  loadInitialRoute();

  return mainWindow;
}

function loadOfflineView() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.loadFile(path.join(__dirname, 'views/offline.html'));
  }
}

function loadSettingsView() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.loadFile(path.join(__dirname, 'views/settings.html'));
  }
}

function loadInitialRoute() {
  if (!mainWindow) return;

  const config = loadConfig();

  if (config.offlineMode) {
    loadOfflineView();
    return;
  }

  if (pendingDeepLink) {
    const deepLink = pendingDeepLink;
    pendingDeepLink = null;
    handleDeepLink(deepLink);
    return;
  }

  mainWindow.loadURL(config.serverUrl).catch((err) => {
    console.warn(`[Main] Could not connect to ${config.serverUrl}: ${err.message}. Loading offline view.`);
    loadOfflineView();
  });
}

function setupIpcHandlers() {
  setupConfigIpc(ipcMain);
  setupKeychainIpc(ipcMain);
  setupOfflineCacheIpc(ipcMain);

  ipcMain.handle('nav:open-external', async (event, url) => {
    if (url && (url.startsWith('http://') || url.startsWith('https://') || url.startsWith('mailto:'))) {
      await shell.openExternal(url);
      return { success: true };
    }
    return { success: false, error: 'Invalid URL' };
  });

  ipcMain.handle('nav:open-settings', async () => {
    loadSettingsView();
    return { success: true };
  });

  ipcMain.handle('nav:open-offline', async () => {
    loadOfflineView();
    return { success: true };
  });

  ipcMain.handle('nav:reload-app', async () => {
    const config = loadConfig();
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.loadURL(config.serverUrl).catch(() => {
        loadOfflineView();
      });
    }
    return { success: true };
  });

  ipcMain.handle('nav:navigate-to', async (event, routePath) => {
    const config = loadConfig();
    const cleanPath = routePath.startsWith('/') ? routePath : `/${routePath}`;
    const targetUrl = `${config.serverUrl.replace(/\/+$/, '')}${cleanPath}`;
    if (mainWindow && !mainWindow.isDestroyed()) {
      await mainWindow.loadURL(targetUrl);
    }
    return { success: true, url: targetUrl };
  });

  ipcMain.handle('system:app-info', async () => {
    return {
      name: app.getName(),
      version: app.getVersion(),
      platform: process.platform,
      arch: process.arch,
      userData: app.getPath('userData'),
    };
  });
}

// App lifecycle
if (isRunningInElectron) {
  app.whenReady().then(() => {
    const win = createMainWindow();

    const trayIconPath = path.join(__dirname, '../assets/tray-icon.png');
    const tray = createTray({
      getMainWindow: () => mainWindow,
      onOpenSettings: () => loadSettingsView(),
      onOpenOffline: () => loadOfflineView(),
      onCheckUpdates: () => {
        if (mainWindow) {
          mainWindow.webContents.send('updater:status', { status: 'checking' });
        }
      },
      onQuit: () => app.quit(),
      iconPath: trayIconPath,
    });

    approvalsMonitor = new ApprovalsMonitor({
      getMainWindow: () => mainWindow,
      onBadgeUpdate: (count) => {
        const activeTray = getTrayInstance();
        if (activeTray && typeof activeTray.updateBadge === 'function') {
          activeTray.updateBadge(count);
        }
      },
      iconPath: path.join(__dirname, '../assets/icon.png'),
      pollIntervalMs: loadConfig().pollIntervalMs || 30000,
    });

    setupNotificationsIpc(ipcMain, approvalsMonitor);
    approvalsMonitor.start();

    initUpdater(win);
    setupUpdaterIpc(ipcMain, win);

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        createMainWindow();
      } else if (mainWindow) {
        mainWindow.show();
        mainWindow.focus();
      }
    });
  });

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') {
      destroyTray();
      if (approvalsMonitor) {
        approvalsMonitor.stop();
      }
      app.quit();
    }
  });

  app.on('before-quit', () => {
    destroyTray();
    if (approvalsMonitor) {
      approvalsMonitor.stop();
    }
  });
}

module.exports = {
  createMainWindow,
  isAllowedUrl,
  handleDeepLink,
  setupIpcHandlers,
};
