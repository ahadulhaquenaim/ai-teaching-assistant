"""Retry helpers with exponential backoff for external API calls.

Free tiers (Gemini, Pinecone, OpenRouter) rate-limit aggressively, so every
outbound call that can hit a 429 or a transient 5xx goes through `with_retry`.
Error classification is duck-typed because each SDK raises its own exception
types; we look for an HTTP status on the common attribute names.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

_RATE_LIMIT_MARKERS = ("rate limit", "rate_limit", "resource_exhausted", "quota", "too many requests")


def extract_status_code(exc: BaseException) -> int | None:
    """Best-effort HTTP status from an SDK exception."""
    for attr in ("status_code", "status", "code", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def is_rate_limit_error(exc: BaseException) -> bool:
    """True if the exception represents an HTTP 429 / quota error."""
    if extract_status_code(exc) == 429:
        return True
    text = str(exc).lower()
    return "429" in text or any(marker in text for marker in _RATE_LIMIT_MARKERS)


def is_transient_error(exc: BaseException) -> bool:
    """True for errors worth retrying: rate limits, 5xx, timeouts, dropped connections."""
    if is_rate_limit_error(exc):
        return True
    status = extract_status_code(exc)
    if status is not None and 500 <= status < 600:
        return True
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return True
    name = type(exc).__name__.lower()
    return any(s in name for s in ("timeout", "connection", "serviceunavailable"))


def _log_retry(state: RetryCallState) -> None:
    exc = state.outcome.exception() if state.outcome else None
    logger.warning(
        "retrying external call",
        extra={
            "attempt": state.attempt_number,
            "error_type": type(exc).__name__ if exc else None,
            "status": extract_status_code(exc) if exc else None,
            "sleep_s": round(state.next_action.sleep, 2) if state.next_action else None,
        },
    )


async def with_retry(
    func: Callable[[], Awaitable[T]],
    *,
    attempts: int = 5,
    initial_delay: float = 2.0,
    max_delay: float = 60.0,
    retry_if: Callable[[BaseException], bool] = is_transient_error,
) -> T:
    """Await `func()` and retry transient failures with exponential backoff + jitter.

    The last exception is re-raised unchanged once attempts are exhausted.
    """
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential_jitter(initial=initial_delay, max=max_delay),
        retry=retry_if_exception(retry_if),
        before_sleep=_log_retry,
        reraise=True,
    ):
        with attempt:
            return await func()
    raise AssertionError("unreachable")  # pragma: no cover
