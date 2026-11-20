"""Base ERP Connector interface, Circuit Breaker, and Rate Limiter."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod

from procure_core.erp.models import (
    ErpCommitmentPayload,
    ErpCommitmentResult,
    ErpConnectionTestResult,
    ErpPurchaseOrderPayload,
    ErpPurchaseOrderResult,
    ErpVendorPayload,
    ErpVendorResult,
)

__all__ = [
    "CircuitBreaker",
    "CircuitBreakerOpenException",
    "ErpConnector",
    "ErpConnectionTestResult",
    "TokenBucketRateLimiter",
]

logger = logging.getLogger(__name__)


class CircuitBreakerOpenException(Exception):
    """Raised when an operation is attempted while the circuit breaker is OPEN."""

    def __init__(self, message: str, consecutive_failures: int) -> None:
        super().__init__(message)
        self.consecutive_failures = consecutive_failures


class CircuitBreaker:
    """Fail-fast circuit breaker defending downstream ERPs from cascading failures."""

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30.0,
        name: str = "erp_circuit_breaker",
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.name = name
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self._lock = asyncio.Lock()

    async def can_execute(self) -> bool:
        """Check whether the circuit allows execution."""
        async with self._lock:
            now = time.time()
            if self.state == "OPEN":
                if now - self.last_failure_time > self.recovery_timeout_seconds:
                    logger.info("CircuitBreaker '%s' entering HALF_OPEN state", self.name)
                    self.state = "HALF_OPEN"
                    return True
                return False
            return True

    async def record_success(self) -> None:
        """Record successful execution and reset failure counters."""
        async with self._lock:
            if self.state != "CLOSED":
                logger.info("CircuitBreaker '%s' reset to CLOSED state after success", self.name)
            self.failure_count = 0
            self.state = "CLOSED"

    async def record_failure(self, error: Exception) -> None:
        """Record failure and trip to OPEN if failure threshold is met."""
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            logger.warning(
                "CircuitBreaker '%s' failure %d/%d: %s",
                self.name,
                self.failure_count,
                self.failure_threshold,
                error,
            )
            if self.failure_count >= self.failure_threshold:
                self.state = "OPEN"
                logger.error(
                    "CIRCUIT BREAKER TRIPPED! '%s' is now OPEN. Fast-failing future calls.",
                    self.name,
                )


class TokenBucketRateLimiter:
    """Thread-safe token bucket rate limiter for ERP call volume."""

    def __init__(self, rate: float = 10.0, capacity: float = 20.0) -> None:
        self.rate = rate  # tokens per second
        self.capacity = capacity
        self.tokens = capacity
        self.last_update = time.time()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> bool:
        """Attempt to acquire tokens, returning True if allowed immediately."""
        async with self._lock:
            now = time.time()
            elapsed = now - self.last_update
            self.last_update = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)

            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    async def wait_and_acquire(
        self,
        tokens: float = 1.0,
        timeout: float = 5.0,  # noqa: ASYNC109
    ) -> bool:
        """Wait until tokens become available or timeout is exceeded."""
        start = time.time()
        while time.time() - start < timeout:
            if await self.acquire(tokens):
                return True
            await asyncio.sleep(0.1)
        return False


class ErpConnector(ABC):
    """Abstract interface defining standard ERP connector operations."""

    def __init__(
        self,
        tenant_id: str,
        connector_type: str,
        rate_limiter: TokenBucketRateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.connector_type = connector_type
        self.rate_limiter = rate_limiter or TokenBucketRateLimiter()
        self.circuit_breaker = circuit_breaker or CircuitBreaker(
            name=f"{connector_type}_{tenant_id}"
        )

    @abstractmethod
    async def test_connection(self) -> ErpConnectionTestResult:
        """Test authentication and connectivity to ERP system."""
        ...

    @abstractmethod
    async def sync_vendor(self, vendor: ErpVendorPayload) -> ErpVendorResult:
        """Synchronize vendor master record with ERP."""
        ...

    @abstractmethod
    async def create_purchase_order(
        self,
        po_payload: ErpPurchaseOrderPayload,
    ) -> ErpPurchaseOrderResult:
        """Create a new Purchase Order in ERP with lookup-before-retry."""
        ...

    @abstractmethod
    async def get_purchase_order(self, po_number: str) -> ErpPurchaseOrderResult | None:
        """Retrieve Purchase Order by PO number from ERP."""
        ...

    @abstractmethod
    async def record_commitment(
        self,
        commitment: ErpCommitmentPayload,
    ) -> ErpCommitmentResult:
        """Record budget commitment in ERP."""
        ...

    @abstractmethod
    async def list_purchase_orders(self, limit: int = 100) -> list[ErpPurchaseOrderResult]:
        """List Purchase Orders directly from ERP records."""
        ...

    @abstractmethod
    async def cancel_purchase_order(self, po_number: str, reason: str = "Order cancelled") -> bool:
        """Cancel or void Purchase Order in ERP."""
        ...
