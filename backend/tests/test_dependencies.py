"""Tests for the stubbed-out auth dependency.

Google login is deferred (see app/core/dependencies.py); every request is
treated as one fixed local user until that phase is implemented.
"""

from __future__ import annotations

from app.core.dependencies import LOCAL_DEV_USER, get_current_user


async def test_get_current_user_returns_fixed_local_user() -> None:
    user = await get_current_user()

    assert user.user_id == "local-dev-user"
    assert user is LOCAL_DEV_USER


async def test_get_current_user_is_stable_across_calls() -> None:
    """Every call must return the same identity (same user_id)."""
    first = await get_current_user()
    second = await get_current_user()

    assert first.user_id == second.user_id


def test_network_is_blocked_in_tests() -> None:
    import socket

    import pytest

    with pytest.raises(RuntimeError, match="Network access is blocked"):
        socket.create_connection(("example.com", 443), timeout=1)
