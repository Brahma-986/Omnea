"""HTTP Client implementation for Omnea Developer Platform REST API."""

from __future__ import annotations

from typing import Any

import httpx

from omnea.exceptions import (
    AuthenticationError,
    NotFoundError,
    OmneaError,
    PermissionDeniedError,
    RateLimitError,
)


class _SubClient:
    def __init__(self, client: OmneaClient) -> None:
        self._c = client


class RequestsClient(_SubClient):
    def create(
        self,
        vendor_name: str,
        estimated_spend: float | str,
        currency: str = "USD",
        department: str = "Engineering",
        description: str = "",
        justification: str = "",
        attachments: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Submit a new procurement request."""
        payload = {
            "vendor_name": vendor_name,
            "estimated_spend": str(estimated_spend),
            "currency": currency,
            "department": department,
            "description": description,
            "justification": justification,
            "attachments": attachments or [],
        }
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return self._c._post("/api/v1/requests", json=payload, headers=headers)

    def get(self, request_id: str) -> dict[str, Any]:
        """Get request details, approval timeline, citations, and reservations."""
        return self._c._get(f"/api/v1/requests/{request_id}")

    def list(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """List procurement requests for organization."""
        return self._c._get(
            "/api/v1/requests", params={"page_size": limit, "page": (offset // limit) + 1}
        )

    def get_decisions(self, request_id: str) -> dict[str, Any]:
        """Get decisions and reviewer verdicts for a request."""
        return self._c._get(f"/api/v1/requests/{request_id}/decisions")


class ApprovalsClient(_SubClient):
    def list_pending(self) -> dict[str, Any]:
        """List pending approvals awaiting human action."""
        return self._c._get("/api/v1/approvals/pending")

    def decide(
        self,
        step_id: str,
        action: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Submit approval decision (approve, reject, request_info)."""
        payload = {"action": action, "reason": reason, "idempotency_key": idempotency_key}
        return self._c._post(f"/api/v1/approvals/{step_id}/decide", json=payload)


class VendorsClient(_SubClient):
    def list(self) -> list[dict[str, Any]]:
        """List vendors in directory."""
        return self._c._get("/api/v1/vendors")

    def get(self, vendor_id: str) -> dict[str, Any]:
        """Get vendor detail."""
        return self._c._get(f"/api/v1/vendors/{vendor_id}")

    def get_relationships(self, vendor_id: str) -> dict[str, Any]:
        """Get vendor corporate tree (parent, subsidiaries, aliases, contracts)."""
        return self._c._get(f"/api/v1/vendors/{vendor_id}/relationships")

    def flag(
        self, vendor_id: str, risk_type: str, severity: str = "high", reason: str = ""
    ) -> dict[str, Any]:
        """Flag vendor for compliance or operational risk."""
        payload = {"risk_type": risk_type, "severity": severity, "reason": reason}
        return self._c._post(f"/api/v1/vendors/{vendor_id}/flag", json=payload)


class DocumentsClient(_SubClient):
    def list(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """List uploaded vendor security reviews / TPRM documents."""
        return self._c._get("/api/v1/documents", params={"limit": limit, "offset": offset})

    def get_assessment(self, document_id: str) -> dict[str, Any]:
        """Get verified TPRM control domain findings."""
        return self._c._get(f"/api/v1/documents/{document_id}/assessment")


class ErpClient(_SubClient):
    def get_status(self) -> dict[str, Any]:
        """Get live ERP connector status."""
        return self._c._get("/api/v1/erp/status")

    def list_purchase_orders(self) -> list[dict[str, Any]]:
        """List purchase orders directly from ERP system of record."""
        return self._c._get("/api/v1/erp/purchase-orders")


class ReconciliationClient(_SubClient):
    def get_findings(self) -> dict[str, Any]:
        """Get drift findings between requests and ERP."""
        return self._c._get("/api/v1/reconciliation/findings")

    def run(self) -> dict[str, Any]:
        """Run on-demand reconciliation audit."""
        return self._c._post("/api/v1/reconciliation/run")


class AuditClient(_SubClient):
    def export(self, format: str = "json", limit: int = 500) -> Any:
        """Export audit trail in JSON or CSV format."""
        return self._c._get("/api/v1/audit/export", params={"format": format, "limit": limit})


class WebhooksClient(_SubClient):
    def list_endpoints(self) -> list[dict[str, Any]]:
        """List registered webhook destination endpoints."""
        return self._c._get("/api/v1/webhooks/endpoints")

    def create_endpoint(
        self,
        url: str,
        events: list[str] | None = None,
        description: str = "",
        secret: str | None = None,
    ) -> dict[str, Any]:
        """Register new outbound webhook destination."""
        payload = {
            "url": url,
            "events": events or ["*"],
            "description": description,
            "secret": secret,
        }
        return self._c._post("/api/v1/webhooks/endpoints", json=payload)

    def delete_endpoint(self, endpoint_id: str) -> dict[str, Any]:
        """Delete webhook endpoint."""
        return self._c._delete(f"/api/v1/webhooks/endpoints/{endpoint_id}")

    def list_deliveries(
        self, endpoint_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """List webhook delivery attempt logs."""
        params = {"limit": limit}
        if endpoint_id:
            params["endpoint_id"] = endpoint_id
        return self._c._get("/api/v1/webhooks/deliveries", params=params)

    def redeliver(self, delivery_id: str) -> dict[str, Any]:
        """Trigger immediate redelivery of prior webhook attempt."""
        return self._c._post(f"/api/v1/webhooks/deliveries/{delivery_id}/redeliver")


class ApiKeysClient(_SubClient):
    def list(self) -> list[dict[str, Any]]:
        """List active and revoked API keys."""
        return self._c._get("/api/v1/api-keys")

    def create(
        self,
        name: str,
        scopes: list[str] | None = None,
        rate_limit_per_minute: int = 60,
    ) -> dict[str, Any]:
        """Create new scoped API key (secret returned once)."""
        payload = {
            "name": name,
            "scopes": scopes or ["requests:read", "requests:create"],
            "rate_limit_per_minute": rate_limit_per_minute,
        }
        return self._c._post("/api/v1/api-keys", json=payload)

    def revoke(self, key_id: str) -> dict[str, Any]:
        """Revoke API key."""
        return self._c._post(f"/api/v1/api-keys/{key_id}/revoke")


class OmneaClient:
    """Official Python Client for the Omnea Procurement Orchestration Platform."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "http://localhost:8000",
        tenant_id: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.tenant_id = tenant_id

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "omnea-python-sdk/0.1.0",
        }
        if self.tenant_id:
            headers["X-Tenant-ID"] = self.tenant_id

        self._http = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout)

        # Namespaced resource clients
        self.requests = RequestsClient(self)
        self.approvals = ApprovalsClient(self)
        self.vendors = VendorsClient(self)
        self.documents = DocumentsClient(self)
        self.erp = ErpClient(self)
        self.reconciliation = ReconciliationClient(self)
        self.audit = AuditClient(self)
        self.webhooks = WebhooksClient(self)
        self.api_keys = ApiKeysClient(self)

    def _handle_response(self, resp: httpx.Response) -> Any:
        if 200 <= resp.status_code < 300:
            if resp.headers.get("content-type", "").startswith("text/csv"):
                return resp.text
            return resp.json()

        try:
            body = resp.json()
        except Exception:
            body = {"detail": resp.text}

        msg = body.get("error", {}).get("message") or body.get("detail") or str(resp.status_code)

        if resp.status_code == 401:
            raise AuthenticationError(msg, status_code=401, response_data=body)
        if resp.status_code == 403:
            raise PermissionDeniedError(msg, status_code=403, response_data=body)
        if resp.status_code == 404:
            raise NotFoundError(msg, status_code=404, response_data=body)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "60"))
            raise RateLimitError(msg, retry_after=retry_after, response_data=body)

        raise OmneaError(
            f"HTTP {resp.status_code}: {msg}", status_code=resp.status_code, response_data=body
        )

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._handle_response(self._http.get(path, params=params))

    def _post(
        self, path: str, json: dict[str, Any] | None = None, headers: dict[str, str] | None = None
    ) -> Any:
        return self._handle_response(self._http.post(path, json=json, headers=headers))

    def _delete(self, path: str) -> Any:
        return self._handle_response(self._http.delete(path))

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> OmneaClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
