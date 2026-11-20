/**
 * Frontend build and asset synchronization script for Vercel Hobby deployment.
 * Copies static assets from services/api/src/api/static to public/ directory for Edge CDN serving.
 */

const fs = require('fs');
const path = require('path');

const ROOT_DIR = path.resolve(__dirname, '..');
const SOURCE_DIR = path.join(ROOT_DIR, 'services', 'api', 'src', 'api', 'static');
const TARGET_DIR = path.join(ROOT_DIR, 'public');
const TARGET_STATIC_DIR = path.join(TARGET_DIR, 'static');

function copyRecursive(src, dest) {
  if (!fs.existsSync(src)) {
    throw new Error(`Source path does not exist: ${src}`);
  }

  const stat = fs.statSync(src);
  if (stat.isDirectory()) {
    if (!fs.existsSync(dest)) {
      fs.mkdirSync(dest, { recursive: true });
    }
    const entries = fs.readdirSync(src);
    for (const entry of entries) {
      copyRecursive(path.join(src, entry), path.join(dest, entry));
    }
  } else {
    const parentDir = path.dirname(dest);
    if (!fs.existsSync(parentDir)) {
      fs.mkdirSync(parentDir, { recursive: true });
    }
    fs.copyFileSync(src, dest);
  }
}

console.log('--- Omnea Frontend Production Build ---');
console.log(`Source static directory: ${SOURCE_DIR}`);
console.log(`Target public directory: ${TARGET_DIR}`);

// 1. Ensure target directory exists
if (!fs.existsSync(TARGET_DIR)) {
  fs.mkdirSync(TARGET_DIR, { recursive: true });
}

// 2. Copy root index.html to public/index.html (SPA entrypoint)
const sourceIndexHtml = path.join(SOURCE_DIR, 'index.html');
const targetIndexHtml = path.join(TARGET_DIR, 'index.html');
fs.copyFileSync(sourceIndexHtml, targetIndexHtml);
console.log('✓ Copied index.html -> public/index.html');

// 3. Copy entire static assets directory to public/static/
copyRecursive(SOURCE_DIR, TARGET_STATIC_DIR);
console.log('✓ Synchronized assets -> public/static/');

// 4. Verify critical assets
const requiredFiles = [
  path.join(TARGET_DIR, 'index.html'),
  path.join(TARGET_STATIC_DIR, 'css', 'tokens.css'),
  path.join(TARGET_STATIC_DIR, 'css', 'components.css'),
  path.join(TARGET_STATIC_DIR, 'css', 'app.css'),
  path.join(TARGET_STATIC_DIR, 'js', 'app.js'),
  path.join(TARGET_STATIC_DIR, 'js', 'api.js'),
  path.join(TARGET_STATIC_DIR, 'js', 'state.js'),
  path.join(TARGET_STATIC_DIR, 'js', 'layout.js'),
  path.join(TARGET_STATIC_DIR, 'js', 'router.js')
];

let allValid = true;
for (const file of requiredFiles) {
  if (!fs.existsSync(file)) {
    console.error(`✗ Missing required build artifact: ${file}`);
    allValid = false;
  }
}

if (!allValid) {
  console.error('Frontend build failed: missing critical assets.');
  process.exit(1);
}

console.log('✓ Frontend build succeeded! All production assets ready for Vercel Edge CDN.');
