'use strict';

const fs = require('fs');
const path = require('path');
const os = require('os');
const assert = require('assert');

// Setup temporary test environment directory for userData
const TEST_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'omnea-desktop-test-'));
process.env.OMNEA_USER_DATA = TEST_DIR;

console.log('====================================================');
console.log(' Omnea ProcureOS Desktop Test Suite');
console.log(` Test Workdir: ${TEST_DIR}`);
console.log('====================================================\n');

let passedTests = 0;
let failedTests = 0;

function runTest(name, fn) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
    passedTests++;
  } catch (err) {
    console.error(`  ✗ ${name}`);
    console.error(`    Error: ${err.message}`);
    failedTests++;
  }
}

async function runAsyncTest(name, fn) {
  try {
    await fn();
    console.log(`  ✓ ${name}`);
    passedTests++;
  } catch (err) {
    console.error(`  ✗ ${name}`);
    console.error(`    Error: ${err.message}`);
    failedTests++;
  }
}

async function runAllTests() {
  console.log('[1/6] Testing Deep Link Protocol Parsing...');
  const deepLink = require('../src/deep_link');

  runTest('parseDeepLink with procure://request/<id>', () => {
    const res = deepLink.parseDeepLink('procure://request/req-12345');
    assert(res !== null, 'Should parse valid request URL');
    assert.strictEqual(res.action, 'request');
    assert.strictEqual(res.id, 'req-12345');
    assert.strictEqual(res.path, '/requests/req-12345');
  });

  runTest('parseDeepLink with procure://requests/<id>?tab=audit', () => {
    const res = deepLink.parseDeepLink('procure://requests/req-abc?tab=audit');
    assert(res !== null);
    assert.strictEqual(res.action, 'request');
    assert.strictEqual(res.id, 'req-abc');
    assert.strictEqual(res.queryParams.tab, 'audit');
  });

  runTest('parseDeepLink with procure://approval/<step_id>', () => {
    const res = deepLink.parseDeepLink('procure://approval/step-9988');
    assert(res !== null);
    assert.strictEqual(res.action, 'approval');
    assert.strictEqual(res.id, 'step-9988');
    assert.strictEqual(res.path, '/approvals/step-9988');
  });

  runTest('parseDeepLink with procure://settings', () => {
    const res = deepLink.parseDeepLink('procure://settings');
    assert(res !== null);
    assert.strictEqual(res.action, 'settings');
    assert.strictEqual(res.path, '/settings');
  });

  runTest('parseDeepLink invalid / non-procure URLs', () => {
    assert.strictEqual(deepLink.parseDeepLink('https://google.com'), null);
    assert.strictEqual(deepLink.parseDeepLink(''), null);
    assert.strictEqual(deepLink.parseDeepLink(null), null);
  });

  runTest('findDeepLinkInArgv finds procure URL in command line', () => {
    const argv = ['electron.exe', '.', '--inspect=5858', 'procure://request/req-999', '--no-sandbox'];
    const found = deepLink.findDeepLinkInArgv(argv);
    assert.strictEqual(found, 'procure://request/req-999');
    assert.strictEqual(deepLink.findDeepLinkInArgv(['--flag1', '--flag2']), null);
  });

  runTest('buildServerUrlForDeepLink formats target server URL', () => {
    const parsed = deepLink.parseDeepLink('procure://request/REQ-77?view=full');
    const target = deepLink.buildServerUrlForDeepLink('http://127.0.0.1:8000', parsed);
    assert.strictEqual(target, 'http://127.0.0.1:8000/requests/REQ-77?view=full');
  });

  console.log('\n[2/6] Testing Configuration Management...');
  const config = require('../src/config');

  runTest('getDefaultConfig returns default serverUrl', () => {
    const def = config.getDefaultConfig();
    assert.strictEqual(def.serverUrl, 'http://127.0.0.1:8000');
    assert.strictEqual(def.pollIntervalMs, 30000);
  });

  runTest('validateServerUrl validates and normalizes URLs', () => {
    assert.strictEqual(config.validateServerUrl('http://127.0.0.1:8000/'), 'http://127.0.0.1:8000');
    assert.strictEqual(config.validateServerUrl('https://procure.omnea.ai///'), 'https://procure.omnea.ai');
    assert.throws(() => config.validateServerUrl('ftp://invalid.com'), /protocol/);
    assert.throws(() => config.validateServerUrl(''), /non-empty/);
  });

  runTest('saveConfig and loadConfig persistence', () => {
    const saved = config.saveConfig({ serverUrl: 'http://localhost:9000', pollIntervalMs: 15000 });
    assert.strictEqual(saved.serverUrl, 'http://localhost:9000');
    assert.strictEqual(saved.pollIntervalMs, 15000);

    const loaded = config.loadConfig();
    assert.strictEqual(loaded.serverUrl, 'http://localhost:9000');
    assert.strictEqual(loaded.pollIntervalMs, 15000);
  });

  await runAsyncTest('testConnection handles unreachable endpoints gracefully', async () => {
    // Unused port
    const res = await config.testConnection('http://127.0.0.1:59999', 500);
    assert.strictEqual(res.ok, false);
    assert(res.error || res.status === 0);
  });

  console.log('\n[3/6] Testing Keychain & Encryption Enclave...');
  const keychain = require('../src/keychain');

  runTest('fallback AES-256-GCM encryption and decryption roundtrip', () => {
    const payload = JSON.stringify({
      access_token: 'omnea_jwt_test_token_123456789',
      refresh_token: 'omnea_refresh_token_abcdef',
      user: { id: 'usr-001', email: 'procurement-lead@omnea.ai' },
    });

    const encrypted = keychain.fallbackEncrypt(payload);
    assert(Buffer.isBuffer(encrypted));
    assert(encrypted.length > 30, 'Ciphertext should contain header, IV, tag, and encrypted body');
    // Verify ciphertext does not contain plaintext string
    assert(!encrypted.toString('utf8').includes('omnea_jwt_test_token'), 'Plaintext must not appear in ciphertext');

    const decrypted = keychain.fallbackDecrypt(encrypted);
    assert.strictEqual(decrypted, payload, 'Decrypted text must match original payload exactly');
  });

  runTest('fallback AES-256-GCM rejects tampered ciphertext or auth tag', () => {
    const payload = 'secret data';
    const encrypted = keychain.fallbackEncrypt(payload);
    // Tamper with the last byte
    encrypted[encrypted.length - 1] ^= 0xFF;
    assert.throws(() => {
      keychain.fallbackDecrypt(encrypted);
    }, /Unsupported|Unsupported state|auth|tag/i);
  });

  runTest('saveTokens, getTokens, and clearTokens lifecycle', () => {
    const testTokens = {
      access_token: 'tok_live_stage8_verified',
      expires_in: 3600,
    };

    const saveRes = keychain.saveTokens(testTokens);
    assert.strictEqual(saveRes.success, true);
    assert(saveRes.method === 'safeStorage' || saveRes.method === 'aes-256-gcm');

    const retrieved = keychain.getTokens();
    assert.deepStrictEqual(retrieved, testTokens);

    const clearRes = keychain.clearTokens();
    assert.strictEqual(clearRes.success, true);
    assert.strictEqual(keychain.getTokens(), null);
  });

  console.log('\n[4/6] Testing Offline Cache...');
  const offlineCache = require('../src/offline_cache');

  runTest('getCachedRequests returns empty default initially', () => {
    offlineCache.clearCache();
    const cache = offlineCache.getCachedRequests();
    assert.strictEqual(cache.count, 0);
    assert(Array.isArray(cache.requests));
    assert.strictEqual(cache.cachedAt, null);
  });

  runTest('saveCachedRequests persists array of requests with timestamp', () => {
    const mockRequests = [
      {
        step_id: 'step-001',
        request_id: 'req-101',
        step_name: 'Security Review',
        vendor_name: 'CrowdStrike Inc',
        estimated_spend: 45000,
        currency: 'USD',
        sla_status: 'on_track',
      },
      {
        step_id: 'step-002',
        request_id: 'req-102',
        step_name: 'VP Finance Approval',
        vendor_name: 'Snowflake Computing',
        estimated_spend: 120000,
        currency: 'USD',
        sla_status: 'escalated',
      },
    ];

    const saved = offlineCache.saveCachedRequests(mockRequests);
    assert.strictEqual(saved.count, 2);
    assert(saved.cachedAt !== null);

    const retrieved = offlineCache.getCachedRequests();
    assert.strictEqual(retrieved.count, 2);
    assert.strictEqual(retrieved.requests[0].vendor_name, 'CrowdStrike Inc');
    assert.strictEqual(retrieved.requests[1].sla_status, 'escalated');
  });

  runTest('clearCache empties offline requests file', () => {
    const res = offlineCache.clearCache();
    assert.strictEqual(res.success, true);
    const retrieved = offlineCache.getCachedRequests();
    assert.strictEqual(retrieved.count, 0);
    assert.strictEqual(retrieved.requests.length, 0);
  });

  console.log('\n[5/6] Testing Security Constraints & Navigation Allow-List...');
  const mainModule = require('../src/main');

  runTest('isAllowedUrl strictly matches allowed server origin and local files', () => {
    const serverUrl = 'http://127.0.0.1:8000';

    assert.strictEqual(mainModule.isAllowedUrl('http://127.0.0.1:8000/requests', serverUrl), true);
    assert.strictEqual(mainModule.isAllowedUrl('http://127.0.0.1:8000/api/v1/approvals/pending', serverUrl), true);
    assert.strictEqual(mainModule.isAllowedUrl('file:///C:/Users/ASUS/Desktop/Omnea/desktop/src/views/offline.html', serverUrl), true);

    // Disallow foreign origins
    assert.strictEqual(mainModule.isAllowedUrl('https://evil-hacker.com', serverUrl), false);
    assert.strictEqual(mainModule.isAllowedUrl('http://127.0.0.1:9000', serverUrl), false); // different port
    assert.strictEqual(mainModule.isAllowedUrl('javascript:alert(1)', serverUrl), false);
    assert.strictEqual(mainModule.isAllowedUrl(null, serverUrl), false);
  });

  console.log('\n[6/6] Verifying Files, Package Config & Assets...');

  runTest('package.json structure and dependencies', () => {
    const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, '../package.json'), 'utf8'));
    assert.strictEqual(pkg.name, 'omnea-procure-desktop');
    assert.strictEqual(pkg.version, '1.0.0');
    assert.strictEqual(pkg.main, 'src/main.js');
    assert(pkg.dependencies && pkg.dependencies['electron-updater']);
    assert(pkg.devDependencies && pkg.devDependencies['electron']);
    assert(pkg.devDependencies && pkg.devDependencies['electron-builder']);
    assert.strictEqual(pkg.build.appId, 'ai.omnea.procure');
    assert.strictEqual(pkg.build.productName, 'Omnea ProcureOS');
    assert(Array.isArray(pkg.build.protocols));
    assert.strictEqual(pkg.build.protocols[0].schemes[0], 'procure');
  });

  runTest('assets exist and are non-empty', () => {
    const assets = ['icon.png', 'tray-icon.png', 'icon.svg', 'tray-icon.svg'];
    for (const file of assets) {
      const p = path.join(__dirname, '../assets', file);
      assert(fs.existsSync(p), `Asset ${file} must exist`);
      assert(fs.statSync(p).size > 50, `Asset ${file} must not be empty`);
    }
  });

  runTest('views exist and contain required markup', () => {
    const offlineHtml = fs.readFileSync(path.join(__dirname, '../src/views/offline.html'), 'utf8');
    assert(offlineHtml.includes('procureDesktop.cache'), 'offline.html must interact with procureDesktop.cache');
    assert(offlineHtml.includes('Offline Mode'), 'offline.html must display Offline Mode banner');

    const settingsHtml = fs.readFileSync(path.join(__dirname, '../src/views/settings.html'), 'utf8');
    assert(settingsHtml.includes('procureDesktop.config'), 'settings.html must interact with procureDesktop.config');
    assert(settingsHtml.includes('Test Connection'), 'settings.html must have Test Connection button');
  });

  runTest('preload.js defines window.procureDesktop bridge with contextIsolation', () => {
    const preloadJs = fs.readFileSync(path.join(__dirname, '../src/preload.js'), 'utf8');
    assert(preloadJs.includes("contextBridge.exposeInMainWorld('procureDesktop'"), 'Must expose procureDesktop');
    assert(!preloadJs.includes('require(') || preloadJs.includes("require('electron')"), 'Must not expose raw require');
  });

  console.log('\n====================================================');
  console.log(` Summary: ${passedTests} passed, ${failedTests} failed.`);
  console.log('====================================================\n');

  // Clean up temporary test directory
  try {
    fs.rmSync(TEST_DIR, { recursive: true, force: true });
  } catch {
    // Ignore cleanup errors on Windows
  }

  if (failedTests > 0) {
    process.exit(1);
  }
}

runAllTests().catch((err) => {
  console.error('Test execution failed:', err);
  process.exit(1);
});
