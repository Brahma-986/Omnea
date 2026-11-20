"""Vector similarity search tenant isolation validation."""

from __future__ import annotations

from typing import Any


class TenantVectorViolationError(Exception):
    """Raised when a vector similarity SQL query violates tenant isolation."""


def validate_vector_query(
    sql: str,
    params: dict[str, Any],
    expected_tenant_id: str | None = None,
) -> None:
    """Validate that a vector similarity query enforces tenant_id filtering.

    Args:
        sql: The SQL query string to validate.
        params: Parameters dictionary bound to the query.
        expected_tenant_id: Optional expected tenant identifier to enforce.

    Raises:
        TenantVectorViolationError: If sql lacks tenant_id predicate or if
            parameterized tenant_id does not match expected_tenant_id.
    """
    if not sql or "tenant_id" not in sql.lower():
        raise TenantVectorViolationError("Similarity query lacks tenant_id filter predicate")

    if expected_tenant_id is not None:
        if "tenant_id" not in params or str(params["tenant_id"]) != str(expected_tenant_id):
            raise TenantVectorViolationError("Cross-tenant vector query violation")
    else:
        if "tenant_id" not in params or not params["tenant_id"]:
            raise TenantVectorViolationError(
                "Vector query parameters must contain non-empty 'tenant_id'"
            )
