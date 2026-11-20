'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const os = require('os');
const { getUserDataPath } = require('./config');

let electronSafeStorage = null;
try {
  const electron = require('electron');
  if (typeof electron === 'object' && electron !== null && electron.safeStorage) {
    electronSafeStorage = electron.safeStorage;
  }
} catch {
  // Test / Node environment
}

const TOKENS_FILENAME = 'auth_tokens.enc';
const FALLBACK_KEY_FILENAME = '.secure_key';

function getTokensFilePath() {
  return path.join(getUserDataPath(), TOKENS_FILENAME);
}

function getFallbackKeyPath() {
  return path.join(getUserDataPath(), FALLBACK_KEY_FILENAME);
}

/**
 * Ensures a persistent 256-bit symmetric key for AES-256-GCM fallback encryption.
 * When safeStorage is not available (e.g. headless, test environments, or Linux without secret-service),
 * this provides hardware-adjacent authenticated encryption.
 */
function getOrCreateFallbackKey() {
  const keyPath = getFallbackKeyPath();
  const dir = path.dirname(keyPath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }

  if (fs.existsSync(keyPath)) {
    const raw = fs.readFileSync(keyPath);
    if (raw.length === 32) {
      return raw;
    }
  }

  // Derive or generate a 32-byte key
  const machineSeed = `${os.hostname()}-${os.userInfo().username}-omnea-procure-salt`;
  const salt = crypto.randomBytes(16);
  const derived = crypto.pbkdf2Sync(machineSeed, salt, 100000, 32, 'sha256');

  fs.writeFileSync(keyPath, derived, { mode: 0o600 });
  return derived;
}

function fallbackEncrypt(plainText) {
  const key = getOrCreateFallbackKey();
  const iv = crypto.randomBytes(12); // 96-bit IV recommended for GCM
  const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
  const encrypted = Buffer.concat([cipher.update(plainText, 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();

  // Format: [1 byte magic header 0x01] + [12 bytes IV] + [16 bytes Tag] + [Ciphertext]
  const header = Buffer.from([0x01]);
  return Buffer.concat([header, iv, tag, encrypted]);
}

function fallbackDecrypt(buffer) {
  if (!buffer || buffer.length < 29) {
    throw new Error('Encrypted payload too short');
  }
  const magic = buffer[0];
  if (magic !== 0x01) {
    throw new Error(`Unsupported fallback encryption version: ${magic}`);
  }

  const key = getOrCreateFallbackKey();
  const iv = buffer.subarray(1, 13);
  const tag = buffer.subarray(13, 29);
  const ciphertext = buffer.subarray(29);

  const decipher = crypto.createDecipheriv('aes-256-gcm', key, iv);
  decipher.setAuthTag(tag);
  const decrypted = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
  return decrypted.toString('utf8');
}

function isSafeStorageAvailable() {
  return (
    electronSafeStorage !== null &&
    typeof electronSafeStorage.isEncryptionAvailable === 'function' &&
    electronSafeStorage.isEncryptionAvailable()
  );
}

function getEncryptionMethod() {
  return isSafeStorageAvailable() ? 'safeStorage' : 'aes-256-gcm';
}

function saveTokens(tokens) {
  if (!tokens) {
    throw new Error('Tokens must be provided');
  }

  const plainText = typeof tokens === 'string' ? tokens : JSON.stringify(tokens);
  let encryptedBuffer;
  let methodUsed;

  if (isSafeStorageAvailable()) {
    try {
      const encrypted = electronSafeStorage.encryptString(plainText);
      // Format: [1 byte magic header 0xEE for safeStorage] + [safeStorage Buffer]
      encryptedBuffer = Buffer.concat([Buffer.from([0xEE]), encrypted]);
      methodUsed = 'safeStorage';
    } catch (err) {
      console.warn('[Keychain] safeStorage.encryptString failed, falling back to AES-GCM:', err.message);
      encryptedBuffer = fallbackEncrypt(plainText);
      methodUsed = 'aes-256-gcm';
    }
  } else {
    encryptedBuffer = fallbackEncrypt(plainText);
    methodUsed = 'aes-256-gcm';
  }

  const filePath = getTokensFilePath();
  const dir = path.dirname(filePath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }

  fs.writeFileSync(filePath, encryptedBuffer, { mode: 0o600 });
  return { success: true, method: methodUsed };
}

function getTokens() {
  const filePath = getTokensFilePath();
  if (!fs.existsSync(filePath)) {
    return null;
  }

  try {
    const rawBuffer = fs.readFileSync(filePath);
    if (!rawBuffer || rawBuffer.length === 0) {
      return null;
    }

    let decryptedText;
    const magic = rawBuffer[0];

    if (magic === 0xEE && isSafeStorageAvailable()) {
      const payload = rawBuffer.subarray(1);
      decryptedText = electronSafeStorage.decryptString(payload);
    } else if (magic === 0x01) {
      decryptedText = fallbackDecrypt(rawBuffer);
    } else if (isSafeStorageAvailable()) {
      try {
        decryptedText = electronSafeStorage.decryptString(rawBuffer);
      } catch {
        decryptedText = fallbackDecrypt(rawBuffer);
      }
    } else {
      decryptedText = fallbackDecrypt(rawBuffer);
    }

    try {
      return JSON.parse(decryptedText);
    } catch {
      return decryptedText;
    }
  } catch (err) {
    console.error('[Keychain] Error reading/decrypting tokens:', err.message);
    return null;
  }
}

function clearTokens() {
  const filePath = getTokensFilePath();
  try {
    if (fs.existsSync(filePath)) {
      fs.unlinkSync(filePath);
    }
    return { success: true };
  } catch (err) {
    console.error('[Keychain] Failed to clear tokens:', err.message);
    return { success: false, error: err.message };
  }
}

function setupKeychainIpc(ipcMain) {
  if (!ipcMain) return;

  ipcMain.handle('auth:save-tokens', async (event, tokens) => {
    return saveTokens(tokens);
  });

  ipcMain.handle('auth:get-tokens', async () => {
    return getTokens();
  });

  ipcMain.handle('auth:clear-tokens', async () => {
    return clearTokens();
  });

  ipcMain.handle('auth:status', async () => {
    return {
      available: true,
      method: getEncryptionMethod(),
      hasTokens: fs.existsSync(getTokensFilePath()),
    };
  });
}

module.exports = {
  getTokensFilePath,
  getFallbackKeyPath,
  fallbackEncrypt,
  fallbackDecrypt,
  isSafeStorageAvailable,
  getEncryptionMethod,
  saveTokens,
  getTokens,
  clearTokens,
  setupKeychainIpc,
};
