'use strict';

const PROTOCOL_PREFIX = 'procure://';

/**
 * Parses a deep-link URL into structured action, resource, ID, and path.
 * Supported formats:
 * - procure://request/<id>
 * - procure://requests/<id>
 * - procure://approval/<id>
 * - procure://approvals/<id>
 * - procure://settings
 */
function parseDeepLink(rawUrl) {
  if (!rawUrl || typeof rawUrl !== 'string') {
    return null;
  }

  const trimmed = rawUrl.trim();
  if (!trimmed.toLowerCase().startsWith('procure:')) {
    return null;
  }

  try {
    // Normalize URL for standard URL parser
    // procure://request/123 -> http://localhost/request/123
    const normalized = trimmed.replace(/^procure:\/*/, 'http://dummy.local/');
    const urlObj = new URL(normalized);

    const segments = urlObj.pathname.split('/').filter(Boolean);
    const queryParams = Object.fromEntries(urlObj.searchParams.entries());

    if (segments.length === 0) {
      return {
        rawUrl: trimmed,
        action: 'home',
        id: null,
        path: '/',
        queryParams,
      };
    }

    const type = segments[0].toLowerCase();
    const id = segments.length > 1 ? decodeURIComponent(segments[1]) : null;

    if (type === 'request' || type === 'requests') {
      return {
        rawUrl: trimmed,
        action: 'request',
        id: id || null,
        path: id ? `/requests/${encodeURIComponent(id)}` : '/requests',
        queryParams,
      };
    }

    if (type === 'approval' || type === 'approvals') {
      return {
        rawUrl: trimmed,
        action: 'approval',
        id: id || null,
        path: id ? `/approvals/${encodeURIComponent(id)}` : '/approvals/pending',
        queryParams,
      };
    }

    if (type === 'settings') {
      return {
        rawUrl: trimmed,
        action: 'settings',
        id: null,
        path: '/settings',
        queryParams,
      };
    }

    return {
      rawUrl: trimmed,
      action: type,
      id: id || null,
      path: `/${segments.map(encodeURIComponent).join('/')}`,
      queryParams,
    };
  } catch (err) {
    console.error('[DeepLink] Failed to parse deep link:', trimmed, err.message);
    return null;
  }
}

/**
 * Searches process.argv or second-instance commandLine arguments for a procure:// URL.
 */
function findDeepLinkInArgv(argv) {
  if (!Array.isArray(argv)) {
    return null;
  }
  for (const arg of argv) {
    if (typeof arg === 'string' && arg.toLowerCase().startsWith('procure:')) {
      return arg;
    }
  }
  return null;
}

/**
 * Converts a parsed deep link into a target server URL.
 */
function buildServerUrlForDeepLink(serverUrl, deepLinkObj) {
  if (!serverUrl || !deepLinkObj) return null;
  const base = serverUrl.replace(/\/+$/, '');
  const path = deepLinkObj.path.startsWith('/') ? deepLinkObj.path : `/${deepLinkObj.path}`;
  const qs = Object.keys(deepLinkObj.queryParams || {}).length > 0
    ? '?' + new URLSearchParams(deepLinkObj.queryParams).toString()
    : '';
  return `${base}${path}${qs}`;
}

module.exports = {
  PROTOCOL_PREFIX,
  parseDeepLink,
  findDeepLinkInArgv,
  buildServerUrlForDeepLink,
};
