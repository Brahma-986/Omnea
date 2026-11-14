"""Temporal worker entrypoint."""

import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from procure_core.config import get_settings
from procure_core.log_config import get_logger, setup_logging

logger = get_logger(__name__)


async def run_worker() -> None:
    """Connect to Temporal and start listening on the default task queue."""
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)

    logger.info(f"Connecting to Temporal frontend at {settings.TEMPORAL_TARGET_HOST}")
    client = await Client.connect(
        settings.TEMPORAL_TARGET_HOST,
        namespace=settings.TEMPORAL_NAMESPACE,
    )

    task_queue = "procure-core-queue"
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[],
        activities=[],
    )

    logger.info(f"Starting Temporal worker on queue '{task_queue}'")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(run_worker())
