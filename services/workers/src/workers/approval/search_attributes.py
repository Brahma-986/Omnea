"""Temporal Search Attribute Registration and Preflight Verification.

Defines the 7 standard enterprise visibility attributes for Procurement Workflows:
1. TenantId (Keyword): Partition visibility by multi-tenant workspace boundary.
2. RequestId (Keyword): Direct indexing of procurement request UUID.
3. ApprovalStatus (Keyword): State machine queryable lifecycle ('in_review', 'awaiting_human', 'approved', 'rejected', etc.).
4. Department (Keyword): Filter by cost center / business unit (e.g. 'Engineering', 'Marketing').
5. EstimatedSpend (Double): Numerical float for inequality range queries (e.g. EstimatedSpend > 10000.00).
6. VendorId (Keyword): Visibility into all workflows active for a specific resolved vendor.
7. NeedsHuman (Bool): Instant filtering of all requests currently blocked waiting on human sign-off.
"""

from __future__ import annotations

from typing import Any

from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import (
    AddSearchAttributesRequest,
    ListSearchAttributesRequest,
)
from temporalio.client import Client

from procure_core.log_config import get_logger

logger = get_logger(__name__)

REQUIRED_SEARCH_ATTRIBUTES: dict[str, IndexedValueType.ValueType] = {
    "TenantId": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    "RequestId": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    "ApprovalStatus": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    "Department": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    "EstimatedSpend": IndexedValueType.INDEXED_VALUE_TYPE_DOUBLE,
    "VendorId": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
    "NeedsHuman": IndexedValueType.INDEXED_VALUE_TYPE_BOOL,
}


async def preflight_check_search_attributes(
    client: Client,
    namespace: str = "default",
    auto_register: bool = True,
) -> dict[str, str]:
    """Preflight check on worker startup verifying required search attributes exist in Temporal server."""
    logger.info(f"Running search attribute preflight check for namespace '{namespace}'...")
    try:
        resp = await client.operator_service.list_search_attributes(
            ListSearchAttributesRequest(namespace=namespace)
        )
        existing_custom: Any = resp.custom_attributes
    except Exception as e:
        logger.warning(f"Could not list search attributes via OperatorService: {e}")
        existing_custom = {}

    missing: dict[str, IndexedValueType.ValueType] = {}
    for attr_name, expected_type in REQUIRED_SEARCH_ATTRIBUTES.items():
        if attr_name not in existing_custom:
            missing[attr_name] = expected_type

    if missing:
        if auto_register:
            logger.info(f"Registering missing search attributes: {list(missing.keys())}")
            try:
                await client.operator_service.add_search_attributes(
                    AddSearchAttributesRequest(
                        namespace=namespace,
                        search_attributes=missing,
                    )
                )
                logger.info(
                    "Successfully registered missing search attributes via OperatorService."
                )
            except Exception as e:
                logger.error(f"Failed to auto-register search attributes: {e}")
                raise RuntimeError(
                    f"Missing required search attributes on Temporal server: {list(missing.keys())}"
                ) from e
        else:
            raise RuntimeError(
                f"Missing required search attributes on Temporal server: {list(missing.keys())}"
            )

    return {k: str(v) for k, v in REQUIRED_SEARCH_ATTRIBUTES.items()}
