'use strict';

let autoUpdater = null;
try {
  const updaterModule = require('electron-updater');
  autoUpdater = updaterModule.autoUpdater;
} catch {
  // Test / Node environment
}

let updateStatus = {
  status: 'idle', // 'idle' | 'checking' | 'available' | 'not-available' | 'downloading' | 'downloaded' | 'error'
  info: null,
  progress: null,
  error: null,
  lastChecked: null,
};

function initUpdater(mainWindow) {
  if (!autoUpdater) {
    console.warn('[Updater] electron-updater is not available in this environment');
    return null;
  }

  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = true;

  function broadcastStatus(newStatus, payload = {}) {
    updateStatus = {
      ...updateStatus,
      status: newStatus,
      ...payload,
      timestamp: new Date().toISOString(),
    };

    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('updater:status', updateStatus);
    }
  }

  autoUpdater.on('checking-for-update', () => {
    broadcastStatus('checking', { lastChecked: new Date().toISOString() });
  });

  autoUpdater.on('update-available', (info) => {
    broadcastStatus('available', { info, error: null });
  });

  autoUpdater.on('update-not-available', (info) => {
    broadcastStatus('not-available', { info, error: null });
  });

  autoUpdater.on('error', (err) => {
    broadcastStatus('error', { error: err.message || String(err) });
  });

  autoUpdater.on('download-progress', (progressObj) => {
    broadcastStatus('downloading', {
      progress: {
        percent: Math.round(progressObj.percent),
        transferred: progressObj.transferred,
        total: progressObj.total,
        bytesPerSecond: progressObj.bytesPerSecond,
      },
    });
  });

  autoUpdater.on('update-downloaded', (info) => {
    broadcastStatus('downloaded', { info });
  });

  return autoUpdater;
}

function checkForUpdates() {
  if (!autoUpdater) {
    return Promise.resolve({ success: false, error: 'autoUpdater not available' });
  }
  return autoUpdater.checkForUpdates().catch((err) => {
    return { success: false, error: err.message };
  });
}

function downloadUpdate() {
  if (!autoUpdater) {
    return Promise.resolve({ success: false, error: 'autoUpdater not available' });
  }
  return autoUpdater.downloadUpdate().catch((err) => {
    return { success: false, error: err.message };
  });
}

function quitAndInstall() {
  if (!autoUpdater) {
    return false;
  }
  autoUpdater.quitAndInstall(false, true);
  return true;
}

function getUpdateStatus() {
  return updateStatus;
}

function setupUpdaterIpc(ipcMain, mainWindow) {
  if (!ipcMain) return;

  ipcMain.handle('updater:check-for-updates', async () => {
    return await checkForUpdates();
  });

  ipcMain.handle('updater:download-update', async () => {
    return await downloadUpdate();
  });

  ipcMain.handle('updater:quit-and-install', async () => {
    return quitAndInstall();
  });

  ipcMain.handle('updater:get-status', async () => {
    return getUpdateStatus();
  });
}

module.exports = {
  initUpdater,
  checkForUpdates,
  downloadUpdate,
  quitAndInstall,
  getUpdateStatus,
  setupUpdaterIpc,
};
