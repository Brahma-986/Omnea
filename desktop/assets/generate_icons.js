'use strict';

const fs = require('fs');
const path = require('path');
const zlib = require('zlib');

const ASSETS_DIR = __dirname;

const ICON_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">
  <defs>
    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#1e40af" />
      <stop offset="50%" stop-color="#1d4ed8" />
      <stop offset="100%" stop-color="#2563eb" />
    </linearGradient>
    <linearGradient id="shieldGrad" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="0.95" />
      <stop offset="100%" stop-color="#bfdbfe" stop-opacity="0.85" />
    </linearGradient>
    <filter id="dropShadow" x="-10%" y="-10%" width="120%" height="120%">
      <feDropShadow dx="0" dy="4" stdDeviation="8" flood-opacity="0.25" />
    </filter>
  </defs>

  <!-- App Rounded Base -->
  <rect x="16" y="16" width="224" height="224" rx="48" fill="url(#bgGrad)" filter="url(#dropShadow)" />

  <!-- Shield Base Outline -->
  <path d="M128 44 L196 68 V128 C196 172 166 208 128 220 C90 208 60 172 60 128 V68 Z" 
        fill="url(#shieldGrad)" />

  <!-- Inner Procure 'O' Emblem -->
  <circle cx="128" cy="124" r="42" fill="none" stroke="#1d4ed8" stroke-width="16" />
  <circle cx="128" cy="124" r="16" fill="#1e40af" />

  <!-- Checkmark Accents -->
  <path d="M116 124 L124 132 L142 114" fill="none" stroke="#ffffff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" />
</svg>`;

const TRAY_ICON_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">
  <path d="M16 2 L26 6 V16 C26 22 22 27 16 30 C10 27 6 22 6 16 V6 Z" fill="#1d4ed8" />
  <circle cx="16" cy="16" r="6" fill="#ffffff" />
  <circle cx="16" cy="16" r="3" fill="#1d4ed8" />
</svg>`;

function crc32(buf) {
  let table = [];
  for (let i = 0; i < 256; i++) {
    let c = i;
    for (let k = 0; k < 8; k++) {
      c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    }
    table[i] = c;
  }
  let crc = 0 ^ (-1);
  for (let i = 0; i < buf.length; i++) {
    crc = (crc >>> 8) ^ table[(crc ^ buf[i]) & 0xFF];
  }
  return (crc ^ (-1)) >>> 0;
}

function makeChunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const t = Buffer.from(type, 'ascii');
  const crcBuf = Buffer.concat([t, data]);
  const c = Buffer.alloc(4);
  c.writeUInt32BE(crc32(crcBuf), 0);
  return Buffer.concat([len, t, data, c]);
}

function createPng(width, height, pixelFn) {
  const sig = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

  // IHDR chunk: width(4), height(4), bitDepth(1)=8, colorType(1)=6(RGBA), comp(1)=0, filt(1)=0, interlace(1)=0
  const ihdrData = Buffer.alloc(13);
  ihdrData.writeUInt32BE(width, 0);
  ihdrData.writeUInt32BE(height, 4);
  ihdrData[8] = 8;
  ihdrData[9] = 6;
  ihdrData[10] = 0;
  ihdrData[11] = 0;
  ihdrData[12] = 0;
  const ihdr = makeChunk('IHDR', ihdrData);

  // Scanlines with filter byte 0
  const scanlineLength = 1 + width * 4;
  const rawData = Buffer.alloc(scanlineLength * height);

  for (let y = 0; y < height; y++) {
    const rowOffset = y * scanlineLength;
    rawData[rowOffset] = 0; // Filter type 0: None

    for (let x = 0; x < width; x++) {
      const pxOffset = rowOffset + 1 + x * 4;
      const [r, g, b, a] = pixelFn(x, y, width, height);
      rawData[pxOffset] = r;
      rawData[pxOffset + 1] = g;
      rawData[pxOffset + 2] = b;
      rawData[pxOffset + 3] = a;
    }
  }

  const compressed = zlib.deflateSync(rawData);
  const idat = makeChunk('IDAT', compressed);
  const iend = makeChunk('IEND', Buffer.alloc(0));

  return Buffer.concat([sig, ihdr, idat, iend]);
}

function generateAssets() {
  fs.writeFileSync(path.join(ASSETS_DIR, 'icon.svg'), ICON_SVG, 'utf8');
  fs.writeFileSync(path.join(ASSETS_DIR, 'tray-icon.svg'), TRAY_ICON_SVG, 'utf8');

  // Generate 256x256 app icon PNG
  const iconPngBuffer = createPng(256, 256, (x, y, w, h) => {
    // Rounded rect boundary
    const r = 48;
    const dx = Math.min(x, w - 1 - x);
    const dy = Math.min(y, h - 1 - y);

    let inside = true;
    if (dx < r && dy < r) {
      const dist = Math.hypot(r - dx, r - dy);
      if (dist > r) inside = false;
    }

    if (!inside || x < 16 || x > w - 17 || y < 16 || y > h - 17) {
      return [0, 0, 0, 0]; // Transparent
    }

    // Shield/emblem inside
    const cx = w / 2;
    const cy = h / 2;
    const distFromCenter = Math.hypot(x - cx, y - cy);

    if (distFromCenter < 56 && distFromCenter > 32) {
      // White inner ring
      return [255, 255, 255, 255];
    }
    if (distFromCenter <= 32) {
      // Blue inner core
      return [30, 64, 175, 255];
    }

    // Blue background gradient
    const t = (x + y) / (w + h);
    const red = Math.round(29 * (1 - t) + 37 * t);
    const green = Math.round(78 * (1 - t) + 99 * t);
    const blue = Math.round(216 * (1 - t) + 235 * t);
    return [red, green, blue, 255];
  });
  fs.writeFileSync(path.join(ASSETS_DIR, 'icon.png'), iconPngBuffer);

  // Generate 32x32 tray icon PNG
  const trayPngBuffer = createPng(32, 32, (x, y, w, h) => {
    const cx = w / 2;
    const cy = h / 2;
    const d = Math.hypot(x - cx, y - cy);

    if (d > 14) return [0, 0, 0, 0];
    if (d >= 10 && d <= 14) return [29, 78, 216, 255]; // Outer brand blue ring
    if (d >= 5 && d < 10) return [255, 255, 255, 255]; // White inner disc
    return [29, 78, 216, 255]; // Blue center dot
  });
  fs.writeFileSync(path.join(ASSETS_DIR, 'tray-icon.png'), trayPngBuffer);

  console.log('Successfully generated icon.svg, tray-icon.svg, icon.png, tray-icon.png');
}

if (require.main === module) {
  generateAssets();
}

module.exports = { generateAssets };
