"""Neo4j async driver management and connection lifecycle."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase

from procure_core.config import get_settings

logger = logging.getLogger(__name__)

_neo4j_driver: AsyncDriver | None = None
_neo4j_driver_loop: Any = None


async def get_neo4j_driver() -> AsyncDriver:
    """Return the AsyncDriver instance for the current running event loop."""
    global _neo4j_driver, _neo4j_driver_loop
    current_loop = asyncio.get_running_loop()

    if _neo4j_driver is not None:
        if _neo4j_driver_loop != current_loop or (
            _neo4j_driver_loop and _neo4j_driver_loop.is_closed()
        ):
            try:
                await _neo4j_driver.close()
            except Exception:
                pass
            _neo4j_driver = None

    if _neo4j_driver is None:
        settings = get_settings()
        _neo4j_driver = AsyncGraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
            max_connection_pool_size=50,
            connection_acquisition_timeout=30.0,
        )
        _neo4j_driver_loop = current_loop
        logger.info("Initialized Neo4j AsyncDriver at %s", settings.NEO4J_URI)
    return _neo4j_driver


async def close_neo4j_driver() -> None:
    """Close the singleton AsyncDriver instance."""
    global _neo4j_driver, _neo4j_driver_loop
    if _neo4j_driver is not None:
        try:
            await _neo4j_driver.close()
        except Exception:
            pass
        _neo4j_driver = None
        _neo4j_driver_loop = None
        logger.info("Closed Neo4j AsyncDriver")


async def execute_cypher_read(
    cypher: str, parameters: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Execute a parameterized read query on Neo4j."""
    driver = await get_neo4j_driver()
    async with driver.session() as session:
        result = await session.run(cypher, parameters or {})
        records = await result.data()
        return records


async def execute_cypher_write(
    cypher: str, parameters: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Execute a parameterized write query on Neo4j."""
    driver = await get_neo4j_driver()
    async with driver.session() as session:
        result = await session.run(cypher, parameters or {})
        records = await result.data()
        return records
