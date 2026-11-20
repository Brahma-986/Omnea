"""Unit tests verifying the shared frontend QueryCache implementation and its integration
with repeated endpoints (/erp/config, /slack/status, /notifications/preferences, /vendors).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

API_JS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "services"
    / "api"
    / "src"
    / "api"
    / "static"
    / "js"
    / "api.js"
)


@pytest.mark.unit
def test_frontend_query_cache_class_and_exports_exist() -> None:
    """Verify QueryCache class, instance export, and in-flight deduplication logic in api.js."""
    assert API_JS_PATH.is_file(), f"{API_JS_PATH} must exist"
    content = API_JS_PATH.read_text(encoding="utf-8")

    # 1. Check QueryCache class definition
    assert "class QueryCache" in content or "export class QueryCache" in content
    assert "export const queryCache = new QueryCache();" in content

    # 2. Check in-flight promise deduplication and TTL map
    assert "this._inFlight" in content
    assert "this._cache" in content
    assert "this._inFlight.has(key)" in content
    assert "invalidate(keyOrPrefix)" in content


@pytest.mark.unit
def test_frontend_endpoints_use_query_cache() -> None:
    """Verify repeated endpoints are wrapped with queryCache.fetch and invalidated on mutations."""
    content = API_JS_PATH.read_text(encoding="utf-8")

    # 1. ERP config (/api/v1/erp/config)
    assert "queryCache.fetch('/api/v1/erp/config'" in content
    assert "queryCache.invalidate('/api/v1/erp/config')" in content

    # 2. Slack status (/api/v1/slack/status)
    assert "queryCache.fetch('/api/v1/slack/status'" in content
    assert "queryCache.invalidate('/api/v1/slack/status')" in content

    # 3. Notification preferences (/api/v1/notifications/preferences)
    assert "queryCache.fetch('/api/v1/notifications/preferences'" in content
    assert "queryCache.invalidate('/api/v1/notifications/preferences')" in content

    # 4. Vendors list (/api/v1/vendors)
    assert "queryCache.fetch(endpoint" in content or "queryCache.fetch('/api/v1/vendors" in content
    assert "queryCache.invalidate('/api/v1/vendors')" in content


@pytest.mark.unit
def test_query_cache_javascript_behavior_with_node() -> None:
    """Execute a Node.js verification script against the QueryCache implementation from api.js to ensure in-flight

    deduplication, TTL caching, and invalidation work correctly at runtime.
    """
    import shutil
    import subprocess

    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js binary not available in test environment")

    api_content = API_JS_PATH.read_text(encoding="utf-8")
    match = re.search(r"(class QueryCache\s*\{[\s\S]*?\n\})", api_content)
    assert match, "QueryCache class not found in api.js"
    class_def = match.group(1)

    js_code = f"""
{class_def}

async function run() {{
  const cache = new QueryCache(500); // 500ms TTL
  let fetchCount = 0;

  const mockFetcher = async () => {{
    fetchCount++;
    await new Promise(r => setTimeout(r, 20));
    return {{ data: 'test-result', count: fetchCount }};
  }};

  // 1. In-flight deduplication: call concurrently
  const [res1, res2, res3] = await Promise.all([
    cache.fetch('/api/v1/erp/config', mockFetcher),
    cache.fetch('/api/v1/erp/config', mockFetcher),
    cache.fetch('/api/v1/erp/config', mockFetcher),
  ]);

  if (fetchCount !== 1) {{
    throw new Error(`Expected fetchCount 1 during concurrent calls, got ${{fetchCount}}`);
  }}
  if (res1.count !== 1 || res2.count !== 1 || res3.count !== 1) {{
    throw new Error('Concurrent callers did not share identical response');
  }}

  // 2. TTL caching: immediate subsequent call returns cached value without invoking fetcher
  const resCached = await cache.fetch('/api/v1/erp/config', mockFetcher);
  if (fetchCount !== 1) {{
    throw new Error(`Expected fetchCount to remain 1 within TTL, got ${{fetchCount}}`);
  }}

  // 3. Invalidation: invalidating triggers new fetch
  cache.invalidate('/api/v1/erp/config');
  const resAfterInvalidation = await cache.fetch('/api/v1/erp/config', mockFetcher);
  if (fetchCount !== 2) {{
    throw new Error(`Expected fetchCount 2 after invalidation, got ${{fetchCount}}`);
  }}

  console.log('QUERY_CACHE_TESTS_PASSED');
}}

run().catch(err => {{
  console.error(err);
  process.exit(1);
}});
"""

    res = subprocess.run(
        [node_bin, "--input-type=module", "-e", js_code],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, (
        f"Node QueryCache test failed:\nstdout: {res.stdout}\nstderr: {res.stderr}"
    )
    assert "QUERY_CACHE_TESTS_PASSED" in res.stdout
