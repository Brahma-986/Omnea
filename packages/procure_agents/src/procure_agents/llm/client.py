"""Resilient LLM invocation client with structured tool calling, token caps, and retries."""

import asyncio
import json
import random
import time
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

try:
    import litellm

    # Disable litellm default telemetry and verbose logging
    litellm.telemetry = False
except ImportError:
    litellm = None

from procure_core.config import Settings, get_settings
from procure_core.log_config import get_logger

logger = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMClientProtocol(Protocol):
    """Protocol defining structured LLM invocation contract."""

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
    """LiteLLM client enforcing token caps, timeouts, rate-limit retries, and privacy logging."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.model = self.settings.LLM_MODEL
        self.max_tokens = self.settings.LLM_MAX_TOKENS
        self.timeout = self.settings.LLM_TIMEOUT_SECONDS
        self.max_retries = self.settings.LLM_MAX_RETRIES

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
                    "temperature": temperature,
                }
                if self.settings.LLM_API_KEY:
                    kwargs["api_key"] = self.settings.LLM_API_KEY
                if self.settings.LLM_API_BASE:
                    kwargs["api_base"] = self.settings.LLM_API_BASE

                response = await litellm.acompletion(**kwargs)
                latency_ms = round((time.perf_counter() - call_start) * 1000, 2)

                usage = getattr(response, "usage", None)
                prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
                completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
                cost = litellm.completion_cost(completion_response=response) if response else 0.0

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


class FakeLLMClient:
    """Mock LLM client for deterministic unit testing without external network calls."""

    def __init__(
        self,
        canned_responses: dict[str, BaseModel] | None = None,
        default_response: BaseModel | None = None,
    ) -> None:
        self.canned_responses = canned_responses or {}
        self.default_response = default_response
        self.call_history: list[dict[str, Any]] = []

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
