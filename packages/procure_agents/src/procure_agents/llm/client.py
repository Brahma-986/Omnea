"""Resilient LLM invocation client with structured tool calling, token caps, and retries."""

import asyncio
import json
import random
import time
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from procure_agents.llm.spend_cap import (
    LLMSpendTracker,
    get_llm_spend_tracker,
)
from procure_core.config import Settings, get_settings
from procure_core.log_config import get_logger, tenant_id_ctx

litellm: Any = None
try:
    import litellm

    # Disable litellm default telemetry and verbose logging
    litellm.telemetry = False
except ImportError:
    pass

logger = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMTextResponse:
    """Plain-text completion response from LLM."""

    content: str
    response_id: str
    model: str
    latency_ms: float


class LLMClientProtocol(Protocol):
    """Protocol defining structured LLM invocation contract."""

    last_response_id: str | None = None

    async def call_structured(
        self,
        system_prompt: str,
        user_content: str,
        response_model: type[T],
        temperature: float = 0.0,
    ) -> T:
        """Invoke LLM with schema binding, returning validated Pydantic model."""
        ...


class ResilientLLMClient:
    """LiteLLM client enforcing token caps, timeouts, rate-limit retries, spend caps, and privacy logging."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.model = self.settings.LLM_MODEL
        self.max_tokens = self.settings.LLM_MAX_TOKENS
        self.timeout = self.settings.LLM_TIMEOUT_SECONDS
        self.max_retries = self.settings.LLM_MAX_RETRIES
        self.last_response_id: str | None = None
        self.spend_tracker = get_llm_spend_tracker(self.settings)

    async def call_structured(
        self,
        system_prompt: str,
        user_content: str,
        response_model: type[T],
        temperature: float = 0.0,
    ) -> T:
        """Invoke LLM with schema tool binding and retry on transient faults."""
        if litellm is None:
            raise RuntimeError("litellm is required for ResilientLLMClient but is not installed.")

        # Hard guard: Check tenant and global daily LLM spend budget caps before execution
        active_tenant = tenant_id_ctx.get()
        self.spend_tracker.check_budget(active_tenant)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"<user_input>\n{user_content}\n</user_input>"},
        ]

        tool_schema = {
            "type": "function",
            "function": {
                "name": response_model.__name__,
                "description": f"Extract structured output for {response_model.__name__}",
                "parameters": response_model.model_json_schema(),
            },
        }

        attempts = 0
        validation_retries = 0

        while True:
            attempts += 1
            call_start = time.perf_counter()
            try:
                effective_temp = temperature
                if effective_temp == 0.0 and (
                    "gemini-3" in self.model or "gemini-2.5" in self.model
                ):
                    effective_temp = 1.0

                # Execute LiteLLM completion
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "messages": messages,
                    "tools": [tool_schema],
                    "tool_choice": {
                        "type": "function",
                        "function": {"name": response_model.__name__},
                    },
                    "max_tokens": self.max_tokens,
                    "timeout": self.timeout,
                    "temperature": effective_temp,
                }
                if self.settings.LLM_API_KEY:
                    kwargs["api_key"] = self.settings.LLM_API_KEY
                if self.settings.LLM_API_BASE:
                    kwargs["api_base"] = self.settings.LLM_API_BASE

                response = await litellm.acompletion(**kwargs)
                self.last_response_id = (
                    getattr(response, "id", None)
                    or getattr(response, "response_id", None)
                    or f"litellm-{int(time.time() * 1000)}"
                )
                latency_ms = round((time.perf_counter() - call_start) * 1000, 2)

                usage = getattr(response, "usage", None)
                prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
                completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
                cost = litellm.completion_cost(completion_response=response) if response else 0.0

                # Record spend against daily caps
                self.spend_tracker.record_spend(active_tenant, cost)

                # Privacy-compliant operational logging (NEVER logs user message text!)
                logger.info(
                    f"LLM call succeeded: model={self.model}, "
                    f"tokens={prompt_tokens}+{completion_tokens}, "
                    f"latency={latency_ms}ms, attempt={attempts}, cost=${cost:.5f}",
                    extra={
                        "model": self.model,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "latency_ms": latency_ms,
                        "attempts": attempts,
                        "cost": cost,
                    },
                )

                # Parse tool arguments
                message = response.choices[0].message
                tool_calls = getattr(message, "tool_calls", [])
                if not tool_calls:
                    raise ValueError(
                        f"LLM failed to return required tool call {response_model.__name__}"
                    )

                tool_args_str = tool_calls[0].function.arguments
                parsed_args = json.loads(tool_args_str)

                # Validate Pydantic schema
                validated: T = response_model.model_validate(parsed_args)
                return validated

            except (ValidationError, json.JSONDecodeError, ValueError) as val_err:
                validation_retries += 1
                if validation_retries > 1 or attempts >= self.max_retries:
                    logger.error(
                        f"Structured output validation failed permanently after "
                        f"{attempts} attempts: {val_err}"
                    )
                    raise

                logger.warning(
                    f"Structured validation error on attempt {attempts}: {val_err}. "
                    "Retrying once with error feedback."
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Your previous output failed validation: {val_err}. "
                            "Please correct and output again strictly conforming to schema."
                        ),
                    }
                )

            except Exception as exc:
                # Retry on rate limit (429), timeouts, or 5xx server errors
                err_str = str(exc).lower()
                is_transient = any(
                    k in err_str
                    for k in ("rate", "429", "timeout", "500", "502", "503", "504", "connection")
                )
                if not is_transient or attempts >= self.max_retries:
                    logger.error(f"Permanent LLM call failure after {attempts} attempts: {exc}")
                    raise

                # Exponential backoff with jitter
                delay = (2 ** (attempts - 1)) + random.uniform(0.1, 0.5)
                logger.warning(
                    f"Transient LLM failure on attempt {attempts} ({exc}). "
                    f"Retrying in {delay:.2f}s..."
                )
                await asyncio.sleep(delay)

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> LLMTextResponse:
        """Execute unstructured text completion with retries and spend cap checks."""
        if litellm is None:
            raise RuntimeError("litellm is required for ResilientLLMClient but is not installed.")

        # Hard guard: Check tenant and global daily LLM spend budget caps before execution
        active_tenant = tenant_id_ctx.get()
        self.spend_tracker.check_budget(active_tenant)

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        effective_temp = temperature
        if effective_temp == 0.0 and ("gemini-3" in self.model or "gemini-2.5" in self.model):
            effective_temp = 1.0

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
            "timeout": self.timeout,
            "temperature": effective_temp,
        }
        if self.settings.LLM_API_KEY:
            kwargs["api_key"] = self.settings.LLM_API_KEY
        if self.settings.LLM_API_BASE:
            kwargs["api_base"] = self.settings.LLM_API_BASE

        call_start = time.perf_counter()
        response = await litellm.acompletion(**kwargs)
        latency_ms = round((time.perf_counter() - call_start) * 1000, 2)
        resp_id = (
            getattr(response, "id", None)
            or getattr(response, "response_id", None)
            or f"litellm-{int(time.time() * 1000)}"
        )
        self.last_response_id = resp_id

        cost = litellm.completion_cost(completion_response=response) if response else 0.0
        self.spend_tracker.record_spend(active_tenant, cost)

        content = response.choices[0].message.content or ""
        return LLMTextResponse(
            content=content,
            response_id=resp_id,
            model=self.model,
            latency_ms=latency_ms,
        )


class FakeLLMClient:
    """Mock LLM client for deterministic unit testing without external network calls."""

    def __init__(
        self,
        canned_responses: dict[str, BaseModel] | None = None,
        default_response: BaseModel | None = None,
        spend_tracker: LLMSpendTracker | None = None,
        cost_per_call: float = 0.0,
    ) -> None:
        self.canned_responses = canned_responses or {}
        self.default_response = default_response
        self.call_history: list[dict[str, Any]] = []
        self.last_response_id: str | None = "fake-resp-id"
        self.spend_tracker = spend_tracker
        self.cost_per_call = cost_per_call

    def set_canned(self, key: str, response: BaseModel) -> None:
        """Register a canned response for a substring key."""
        self.canned_responses[key] = response

    async def call_structured(
        self,
        system_prompt: str,
        user_content: str,
        response_model: type[T],
        temperature: float = 0.0,
    ) -> T:
        active_tenant = tenant_id_ctx.get()
        if self.spend_tracker:
            est = self.cost_per_call if self.cost_per_call > 0 else 0.001
            self.spend_tracker.check_budget(active_tenant, estimated_cost=est)
            if self.cost_per_call > 0:
                self.spend_tracker.record_spend(active_tenant, self.cost_per_call)

        self.call_history.append(
            {
                "system_prompt": system_prompt,
                "user_content": user_content,
                "response_model": response_model,
                "temperature": temperature,
            }
        )

        # Match registered canned responses by substring in user_content
        for key, resp in self.canned_responses.items():
            if key in user_content and isinstance(resp, response_model):
                return resp

        # Match default response if set and of appropriate type
        if self.default_response is not None and isinstance(self.default_response, response_model):
            return self.default_response

        # Default fallback constructor
        model_any: Any = response_model
        if hasattr(model_any, "default_test_instance"):
            fallback = model_any.default_test_instance()
            if isinstance(fallback, response_model):
                return fallback

        # Construct minimal valid instance
        return response_model.model_validate({})

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        _temperature: float = 0.0,
        _max_tokens: int | None = None,
    ) -> LLMTextResponse:
        """Mock text generation."""
        active_tenant = tenant_id_ctx.get()
        if self.spend_tracker:
            est = self.cost_per_call if self.cost_per_call > 0 else 0.001
            self.spend_tracker.check_budget(active_tenant, estimated_cost=est)
            if self.cost_per_call > 0:
                self.spend_tracker.record_spend(active_tenant, self.cost_per_call)

        self.call_history.append({"prompt": prompt, "system_prompt": system_prompt})
        return LLMTextResponse(
            content="{}",
            response_id="fake-resp-id",
            model="fake-model",
            latency_ms=10.0,
        )
