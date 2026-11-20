"""Storage boundary and tenant prefix validation."""

from __future__ import annotations


class TenantStorageViolationError(Exception):
    """Raised when an S3 storage operation violates tenant isolation boundaries."""


def validate_s3_tenant_prefix(tenant_id: str, key: str) -> None:
    """Validate that an S3 storage key strictly resides within the tenant's prefix.

    Args:
        tenant_id: The expected tenant identifier.
        key: The object storage key to validate.

    Raises:
        TenantStorageViolationError: If tenant_id or key is missing, if key does
            not start with tenant prefix, or if path traversal is detected.
    """
    if not key or not tenant_id:
        raise TenantStorageViolationError("Missing tenant_id or key")

    if not key.startswith(f"{tenant_id}/"):
        raise TenantStorageViolationError(
            f"Storage key '{key}' violates tenant boundary for tenant '{tenant_id}'"
        )

    if ".." in key or "//" in key:
        raise TenantStorageViolationError(f"Path traversal detected in storage key '{key}'")
