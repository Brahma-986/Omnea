'use strict';

const { contextBridge, ipcRenderer } = require('electron');

/**
 * Safe, isolated API bridge exposed to the renderer process.
 * Context isolation is strictly enforced; no raw Node or Electron APIs are leaked.
 */
const procureDesktopAPI = {
  // Configuration management
  config: {
    get: () => ipcRenderer.invoke('config:get'),
    set: (updates) => ipcRenderer.invoke('config:set', updates),
    testConnection: (targetUrl) => ipcRenderer.invoke('config:test-connection', targetUrl),
  },

  // Secure OS Keychain / Tokens
  auth: {
    saveTokens: (tokens) => ipcRenderer.invoke('auth:save-tokens', tokens),
    getTokens: () => ipcRenderer.invoke('auth:get-tokens'),
    clearTokens: () => ipcRenderer.invoke('auth:clear-tokens'),
    getStatus: () => ipcRenderer.invoke('auth:status'),
  },

  // Offline Cache for Requests & Approvals
  cache: {
    saveRequests: (requests) => ipcRenderer.invoke('cache:save-requests', requests),
    getRequests: () => ipcRenderer.invoke('cache:get-requests'),
    clear: () => ipcRenderer.invoke('cache:clear'),
  },

  // Background Approvals & Notifications
  notifications: {
    checkNow: () => ipcRenderer.invoke('notifications:check-now'),
    getStatus: () => ipcRenderer.invoke('notifications:get-status'),
    onApprovalSelected: (callback) => {
      if (typeof callback !== 'function') return () => {};
      const subscription = (event, step) => callback(step);
      ipcRenderer.on('approval:selected', subscription);
      return () => ipcRenderer.removeListener('approval:selected', subscription);
    },
  },

  // Deep Linking
  deepLink: {
    onDeepLink: (callback) => {
      if (typeof callback !== 'function') return () => {};
      const subscription = (event, linkData) => callback(linkData);
      ipcRenderer.on('deeplink:received', subscription);
      return () => ipcRenderer.removeListener('deeplink:received', subscription);
    },
  },

  // Auto Updater
  updater: {
    checkForUpdates: () => ipcRenderer.invoke('updater:check-for-updates'),
    downloadUpdate: () => ipcRenderer.invoke('updater:download-update'),
    quitAndInstall: () => ipcRenderer.invoke('updater:quit-and-install'),
    getStatus: () => ipcRenderer.invoke('updater:get-status'),
    onStatus: (callback) => {
      if (typeof callback !== 'function') return () => {};
      const subscription = (event, status) => callback(status);
      ipcRenderer.on('updater:status', subscription);
      return () => ipcRenderer.removeListener('updater:status', subscription);
    },
  },

  // Navigation & Window Operations
  navigation: {
    openExternal: (url) => ipcRenderer.invoke('nav:open-external', url),
    openSettings: () => ipcRenderer.invoke('nav:open-settings'),
    openOffline: () => ipcRenderer.invoke('nav:open-offline'),
    reloadApp: () => ipcRenderer.invoke('nav:reload-app'),
    navigateTo: (path) => ipcRenderer.invoke('nav:navigate-to', path),
    onNavigatePending: (callback) => {
      if (typeof callback !== 'function') return () => {};
      const subscription = () => callback();
      ipcRenderer.on('navigate:pending', subscription);
      return () => ipcRenderer.removeListener('navigate:pending', subscription);
    },
  },

  // System & Platform Info
  system: {
    getAppInfo: () => ipcRenderer.invoke('system:app-info'),
  },
};

contextBridge.exposeInMainWorld('procureDesktop', procureDesktopAPI);
