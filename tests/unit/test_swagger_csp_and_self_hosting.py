"""Tests verifying Swagger UI self-hosting, strict CSP headers, and zero external origins."""

from __future__ import annotations

import re

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import create_app


@pytest.mark.unit
@pytest.mark.asyncio
async def test_swagger_docs_endpoint_serves_self_hosted_clean_html() -> None:
    """GET /docs returns 200, has no inline scripts, no inline event handlers, and same-origin assets."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/docs")
        assert res.status_code == 200
        html = res.text

        # 1. Check <script> tags: must only have src, no inline script body
        script_matches = re.findall(r"<script(.*?)>(.*?)</script>", html, re.DOTALL | re.IGNORECASE)
        assert len(script_matches) > 0, "Expected at least one <script> tag for Swagger UI"
        for attrs, body in script_matches:
            assert 'src="' in attrs or "src='" in attrs, (
                f"Found script without src attribute: {attrs}"
            )
            assert not body.strip(), f"Found script with inline body: {body}"

        # 2. Check no on*= inline event handlers (e.g. onload, onclick, onerror)
        inline_handlers = re.findall(r"\son[a-z]+\s*=", html, re.IGNORECASE)
        assert not inline_handlers, f"Found inline event handlers: {inline_handlers}"

        # 3. Check every src and href is same-origin
        all_urls = re.findall(r'(?:src|href)=["\'](.*?)["\']', html, re.IGNORECASE)
        assert len(all_urls) > 0
        for url in all_urls:
            assert (
                not url.startswith("http://")
                and not url.startswith("https://")
                and not url.startswith("//")
            ), f"Found external URL in /docs HTML: {url}"
            assert not any(
                forbidden in url
                for forbidden in [
                    "cdn.jsdelivr.net",
                    "fastapi.tiangolo.com",
                    "unpkg.com",
                    "cdnjs.cloudflare.com",
                ]
            )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_swagger_static_assets_return_200_with_correct_content_types() -> None:
    """GET /static/swagger/... returns 200 with correct MIME types and nosniff header."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. JS bundle
        res_js = await client.get("/static/swagger/swagger-ui-bundle.js")
        assert res_js.status_code == 200
        assert "javascript" in res_js.headers.get("content-type", "").lower()
        assert res_js.headers.get("x-content-type-options") == "nosniff"
        assert len(res_js.content) > 100_000

        # 2. CSS stylesheet
        res_css = await client.get("/static/swagger/swagger-ui.css")
        assert res_css.status_code == 200
        assert "text/css" in res_css.headers.get("content-type", "").lower()
        assert res_css.headers.get("x-content-type-options") == "nosniff"
        assert len(res_css.content) > 10_000

        # 3. Initializer script
        res_init = await client.get("/static/swagger/init.js")
        assert res_init.status_code == 200
        assert "javascript" in res_init.headers.get("content-type", "").lower()

        # 4. Favicon
        res_fav = await client.get("/static/swagger/favicon.png")
        assert res_fav.status_code == 200
        assert "image/" in res_fav.headers.get("content-type", "").lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_docs_csp_headers_strictly_enforced() -> None:
    """Assert response headers on /docs contain script-src 'self' and no external CDN origins."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/docs")
        assert res.status_code == 200
        csp = res.headers.get("content-security-policy", "")

        assert "script-src 'self'" in csp
        assert "default-src 'self'" in csp
        assert "cdn.jsdelivr.net" not in csp
        assert "fastapi.tiangolo.com" not in csp
        assert "unpkg.com" not in csp
        assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0], (
            "script-src must NEVER contain 'unsafe-inline'!"
        )
