"""Cypher graph query tenant isolation validation."""

from __future__ import annotations

from typing import Any


class TenantGraphViolationError(Exception):
    """Raised when a Cypher query violates tenant isolation boundaries."""


def validate_cypher_tenant_isolation(
    query: str,
    params: dict[str, Any],
    expected_tenant_id: str | None = None,
) -> None:
    """Validate that a Neo4j Cypher query enforces tenant isolation.

    Args:
        query: The Cypher query string to validate.
        params: Parameters dictionary bound to the query.
        expected_tenant_id: Optional expected tenant identifier to enforce.

    Raises:
        TenantGraphViolationError: If query does not reference tenant_id or if
            parameterized tenant_id does not match expected_tenant_id.
    """
    if not query or "tenant_id" not in query.lower():
        raise TenantGraphViolationError("Query does not reference tenant_id")

    if expected_tenant_id is not None:
        if "tenant_id" not in params or str(params["tenant_id"]) != str(expected_tenant_id):
            raise TenantGraphViolationError(
                f"Cross-tenant Cypher query violation: expected {expected_tenant_id}, "
                f"got {params.get('tenant_id')}"
            )
    else:
        if "tenant_id" not in params or not params["tenant_id"]:
            raise TenantGraphViolationError(
                "Cypher query parameters must contain non-empty 'tenant_id'"
            )
