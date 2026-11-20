"""LLM daily spend cap tracking and cost runaway prevention."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

from procure_core.config import Settings, get_settings
from procure_core.log_config import get_logger

logger = get_logger(__name__)


class LLMSpendBudgetExceededError(RuntimeError):
    """Raised when an LLM call would exceed the configured global or tenant daily spend cap."""

    pass


class LLMSpendTracker:
    """Thread-safe sliding-day LLM cost tracker enforcing hard spend caps."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._lock = threading.Lock()
        self._current_date: str = self._get_current_utc_date()
        self._global_spend: float = 0.0
        self._tenant_spend: dict[str, float] = {}

    @property
    def global_daily_budget(self) -> float:
        return float(self.settings.LLM_GLOBAL_DAILY_BUDGET_USD)

    @property
    def tenant_daily_budget(self) -> float:
        return float(self.settings.LLM_TENANT_DAILY_BUDGET_USD)

    def _get_current_utc_date(self) -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d")

    def _rollover_if_needed(self) -> None:
        """Reset spend metrics when UTC day rolls over."""
        today = self._get_current_utc_date()
        if today != self._current_date:
            logger.info(
                f"LLM spend daily rollover: resetting spend tracking from {self._current_date} to {today}"
            )
            self._current_date = today
            self._global_spend = 0.0
            self._tenant_spend.clear()

    def check_budget(self, tenant_id: str | None = None, estimated_cost: float = 0.001) -> None:
        """Verify that an upcoming LLM invocation does not exceed global or per-tenant budget caps."""
        with self._lock:
            self._rollover_if_needed()

            # Global daily ceiling
            if (self._global_spend + estimated_cost) > self.global_daily_budget:
                msg = (
                    f"Global LLM daily spend cap of ${self.global_daily_budget:.2f} exceeded "
                    f"(current spend: ${self._global_spend:.4f}). Autonomous LLM calls paused "
                    "to prevent runaway cloud expenditure."
                )
                logger.warning(msg, extra={"global_spend": self._global_spend})
                raise LLMSpendBudgetExceededError(msg)

            # Per-tenant daily ceiling
            if tenant_id:
                t_spend = self._tenant_spend.get(tenant_id, 0.0)
                if (t_spend + estimated_cost) > self.tenant_daily_budget:
                    msg = (
                        f"Tenant '{tenant_id}' LLM daily spend cap of ${self.tenant_daily_budget:.2f} "
                        f"exceeded (current spend: ${t_spend:.4f}). Request blocked."
                    )
                    logger.warning(
                        msg,
                        extra={"tenant_id": tenant_id, "tenant_spend": t_spend},
                    )
                    raise LLMSpendBudgetExceededError(msg)

    def record_spend(self, tenant_id: str | None, cost_usd: float) -> None:
        """Record actual incurred LLM cost after API completion."""
        if cost_usd <= 0.0:
            return

        with self._lock:
            self._rollover_if_needed()
            self._global_spend += cost_usd
            if tenant_id:
                self._tenant_spend[tenant_id] = self._tenant_spend.get(tenant_id, 0.0) + cost_usd

            logger.info(
                f"LLM spend recorded: +${cost_usd:.5f} (global: ${self._global_spend:.4f}/${self.global_daily_budget:.2f})",
                extra={
                    "cost_usd": cost_usd,
                    "global_spend_usd": self._global_spend,
                    "tenant_id": tenant_id,
                    "tenant_spend_usd": self._tenant_spend.get(tenant_id or "", 0.0),
                },
            )

    def get_summary(self, tenant_id: str | None = None) -> dict[str, Any]:
        """Return public and tenant budget status summary."""
        with self._lock:
            self._rollover_if_needed()
            t_spent = self._tenant_spend.get(tenant_id, 0.0) if tenant_id else 0.0
            t_rem = max(0.0, self.tenant_daily_budget - t_spent) if tenant_id else None
            g_rem = max(0.0, self.global_daily_budget - self._global_spend)
            is_exceeded = self._global_spend >= self.global_daily_budget or (
                tenant_id is not None and t_spent >= self.tenant_daily_budget
            )

            res: dict[str, Any] = {
                "date": self._current_date,
                "global_daily_budget_usd": self.global_daily_budget,
                "global_spent_usd": round(self._global_spend, 4),
                "global_remaining_usd": round(g_rem, 4),
                "tenant_daily_budget_usd": self.tenant_daily_budget,
                "status": "budget_exceeded" if is_exceeded else "within_budget",
            }
            if tenant_id:
                res["tenant_id"] = tenant_id
                res["tenant_spent_usd"] = round(t_spent, 4)
                res["tenant_remaining_usd"] = round(t_rem, 4) if t_rem is not None else 0.0
            return res

    def reset(self) -> None:
        """Reset spend metrics (primarily for tests)."""
        with self._lock:
            self._global_spend = 0.0
            self._tenant_spend.clear()
            self._current_date = self._get_current_utc_date()


_global_spend_tracker: LLMSpendTracker | None = None


def get_llm_spend_tracker(settings: Settings | None = None) -> LLMSpendTracker:
    """Singleton getter for LLMSpendTracker."""
    global _global_spend_tracker
    if _global_spend_tracker is None:
        _global_spend_tracker = LLMSpendTracker(settings)
    return _global_spend_tracker
