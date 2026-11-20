"""ERP Connector Factory resolving active connectors per tenant."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from procure_core.erp.base import ErpConnector
from procure_core.erp.connectors import NetSuiteConnector, SapConnector, WorkdayConnector
from procure_core.erp.ledger import LedgerErpConnector
from procure_core.models.erp import ErpConfigurationModel

logger = logging.getLogger(__name__)


async def get_erp_connector(
    session: AsyncSession,
    tenant_id: str,
    connector_type: str | None = None,
) -> ErpConnector:
    """Resolve and return active ERP connector for tenant."""
    # Look up tenant configuration
    stmt = select(ErpConfigurationModel).where(ErpConfigurationModel.tenant_id == tenant_id)
    if connector_type:
        stmt = stmt.where(ErpConfigurationModel.connector_type == connector_type)
    else:
        stmt = stmt.order_by(ErpConfigurationModel.created_at.desc())

    res = await session.execute(stmt)
    config = res.scalar_one_or_none()

    target_type = config.connector_type if config else (connector_type or "ledger")
    secret_ref = config.secret_ref if config else None

    if target_type == "ledger":
        return LedgerErpConnector(tenant_id=tenant_id)
    elif target_type == "netsuite":
        return NetSuiteConnector(tenant_id=tenant_id, secret_ref=secret_ref)
    elif target_type == "sap":
        return SapConnector(tenant_id=tenant_id, secret_ref=secret_ref)
    elif target_type == "workday":
        return WorkdayConnector(tenant_id=tenant_id, secret_ref=secret_ref)
    else:
        logger.warning("Unknown connector type '%s'; defaulting to built-in ledger", target_type)
        return LedgerErpConnector(tenant_id=tenant_id)
