"""Tests for retry classification and exponential backoff."""

from __future__ import annotations

import pytest
from app.services.retry import is_rate_limit_error, is_transient_error, with_retry


class HTTPError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


@pytest.mark.parametrize(
    ("exc", "rate_limited", "transient"),
    [
        (HTTPError(429), True, True),
        (Exception("RESOURCE_EXHAUSTED: quota"), True, True),
        (HTTPError(503), False, True),
        (TimeoutError(), False, True),
        (HTTPError(400), False, False),
        (ValueError("bad input"), False, False),
    ],
)
def test_classification(exc: Exception, rate_limited: bool, transient: bool) -> None:
    assert is_rate_limit_error(exc) is rate_limited
    assert is_transient_error(exc) is transient


async def test_retries_transient_then_succeeds() -> None:
    calls = 0

    async def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise HTTPError(429)
        return "ok"

    assert await with_retry(flaky, attempts=5, initial_delay=0.001, max_delay=0.01) == "ok"
    assert calls == 3


async def test_does_not_retry_permanent_errors() -> None:
    calls = 0

    async def broken() -> None:
        nonlocal calls
        calls += 1
        raise HTTPError(400)

    with pytest.raises(HTTPError):
        await with_retry(broken, attempts=5, initial_delay=0.001)
    assert calls == 1


async def test_gives_up_after_attempts() -> None:
    calls = 0

    async def always_429() -> None:
        nonlocal calls
        calls += 1
        raise HTTPError(429)

    with pytest.raises(HTTPError):
        await with_retry(always_429, attempts=3, initial_delay=0.001, max_delay=0.01)
    assert calls == 3
