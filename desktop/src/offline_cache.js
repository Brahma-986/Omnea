'use strict';

const fs = require('fs');
const path = require('path');
const { getUserDataPath } = require('./config');

const CACHE_FILENAME = 'offline_requests.json';

function getCacheFilePath() {
  return path.join(getUserDataPath(), CACHE_FILENAME);
}

function getDefaultCache() {
  return {
    cachedAt: null,
    count: 0,
    requests: [],
  };
}

function saveCachedRequests(data) {
  let items = [];
  if (Array.isArray(data)) {
    items = data;
  } else if (data && typeof data === 'object') {
    if (Array.isArray(data.requests)) {
      items = data.requests;
    } else if (Array.isArray(data.pending_approvals)) {
      items = data.pending_approvals;
    } else if (Array.isArray(data.items)) {
      items = data.items;
    }
  }

  const cachePayload = {
    cachedAt: new Date().toISOString(),
    count: items.length,
    requests: items,
  };

  const filePath = getCacheFilePath();
  const dir = path.dirname(filePath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }

  fs.writeFileSync(filePath, JSON.stringify(cachePayload, null, 2), 'utf8');
  return cachePayload;
}

function getCachedRequests() {
  const filePath = getCacheFilePath();
  if (!fs.existsSync(filePath)) {
    return getDefaultCache();
  }

  try {
    const raw = fs.readFileSync(filePath, 'utf8');
    const parsed = JSON.parse(raw);
    return {
      cachedAt: parsed.cachedAt || null,
      count: Array.isArray(parsed.requests) ? parsed.requests.length : 0,
      requests: Array.isArray(parsed.requests) ? parsed.requests : [],
    };
  } catch (err) {
    console.error('[OfflineCache] Failed to read cached requests:', err.message);
    return getDefaultCache();
  }
}

function clearCache() {
  const filePath = getCacheFilePath();
  try {
    if (fs.existsSync(filePath)) {
      fs.unlinkSync(filePath);
    }
    return { success: true };
  } catch (err) {
    console.error('[OfflineCache] Failed to clear cache:', err.message);
    return { success: false, error: err.message };
  }
}

function setupOfflineCacheIpc(ipcMain) {
  if (!ipcMain) return;

  ipcMain.handle('cache:save-requests', async (event, requests) => {
    return saveCachedRequests(requests);
  });

  ipcMain.handle('cache:get-requests', async () => {
    return getCachedRequests();
  });

  ipcMain.handle('cache:clear', async () => {
    return clearCache();
  });
}

module.exports = {
  getCacheFilePath,
  getDefaultCache,
  saveCachedRequests,
  getCachedRequests,
  clearCache,
  setupOfflineCacheIpc,
};
