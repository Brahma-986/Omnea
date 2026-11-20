'use strict';

const path = require('path');

let electron = null;
try {
  electron = require('electron');
} catch {
  // Test / Node environment
}

let trayInstance = null;
let currentPendingCount = 0;

function createTray(options = {}) {
  if (!electron || !electron.Tray) {
    return null;
  }

  const {
    getMainWindow = () => null,
    onOpenSettings = () => {},
    onOpenOffline = () => {},
    onCheckUpdates = () => {},
    onQuit = () => { if (electron.app) electron.app.quit(); },
    iconPath = path.join(__dirname, '../assets/tray-icon.png'),
  } = options;

  if (trayInstance) {
    return trayInstance;
  }

  try {
    const { Tray, Menu, nativeImage, app } = electron;
    let icon = iconPath;
    if (typeof nativeImage.createFromPath === 'function') {
      const nIcon = nativeImage.createFromPath(iconPath);
      if (!nIcon.isEmpty()) {
        icon = nIcon;
      }
    }

    trayInstance = new Tray(icon);
    trayInstance.setToolTip('Omnea ProcureOS');

    function rebuildContextMenu() {
      const countLabel = currentPendingCount > 0
        ? `Pending Approvals (${currentPendingCount})`
        : 'Pending Approvals (0)';

      const contextMenu = Menu.buildFromTemplate([
        {
          label: 'Open Omnea ProcureOS',
          click: () => {
            const win = getMainWindow();
            if (win) {
              if (win.isMinimized()) win.restore();
              win.show();
              win.focus();
            }
          },
        },
        {
          label: countLabel,
          enabled: currentPendingCount > 0,
          click: () => {
            const win = getMainWindow();
            if (win) {
              if (win.isMinimized()) win.restore();
              win.show();
              win.focus();
              win.webContents.send('navigate:pending');
            }
          },
        },
        { type: 'separator' },
        {
          label: 'Server Settings...',
          click: onOpenSettings,
        },
        {
          label: 'Offline Cache...',
          click: onOpenOffline,
        },
        {
          label: 'Check for Updates...',
          click: onCheckUpdates,
        },
        { type: 'separator' },
        {
          label: 'Quit Omnea ProcureOS',
          click: onQuit,
        },
      ]);

      trayInstance.setContextMenu(contextMenu);
    }

    trayInstance.on('click', () => {
      const win = getMainWindow();
      if (win) {
        if (win.isVisible()) {
          if (win.isFocused()) {
            win.hide();
          } else {
            win.focus();
          }
        } else {
          win.show();
          win.focus();
        }
      }
    });

    trayInstance.on('double-click', () => {
      const win = getMainWindow();
      if (win) {
        win.show();
        win.focus();
      }
    });

    rebuildContextMenu();

    trayInstance.updateBadge = function (pendingCount) {
      currentPendingCount = Number(pendingCount) || 0;
      const tooltip = currentPendingCount > 0
        ? `Omnea ProcureOS - ${currentPendingCount} pending approval${currentPendingCount > 1 ? 's' : ''}`
        : 'Omnea ProcureOS';

      trayInstance.setToolTip(tooltip);

      // macOS title badge
      if (process.platform === 'darwin' && typeof trayInstance.setTitle === 'function') {
        trayInstance.setTitle(currentPendingCount > 0 ? ` ${currentPendingCount}` : '');
      }

      // Windows taskbar badge (if app badge supported)
      if (app && typeof app.setBadgeCount === 'function') {
        app.setBadgeCount(currentPendingCount);
      }

      rebuildContextMenu();
    };

    return trayInstance;
  } catch (err) {
    console.error('[Tray] Failed to initialize tray:', err.message);
    return null;
  }
}

function getTrayInstance() {
  return trayInstance;
}

function destroyTray() {
  if (trayInstance) {
    trayInstance.destroy();
    trayInstance = null;
  }
}

module.exports = {
  createTray,
  getTrayInstance,
  destroyTray,
};
