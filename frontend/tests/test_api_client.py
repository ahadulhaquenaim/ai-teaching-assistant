"""Tests for api_client: errors, cold-start retries, auth header."""

from __future__ import annotations

from typing import Any

import api_client
import httpx
import pytest


def response(status: int, json: Any = None, text: str = "") -> httpx.Response:
    request = httpx.Request("GET", "http://test/x")
    if json is not None:
        return httpx.Response(status, json=json, request=request)
    return httpx.Response(status, text=text, request=request)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_client.time, "sleep", lambda s: None)
    monkeypatch.setattr(api_client, "backend_url", lambda: "http://test")


def sequence(monkeypatch: pytest.MonkeyPatch, *outcomes: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    queue = list(outcomes)

    def fake_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        calls.append({"method": method, "url": url, **kwargs})
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(api_client.httpx, "request", fake_request)
    return calls


def test_success_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    sequence(monkeypatch, response(200, {"ok": True}))
    assert api_client.request("GET", "/x") == {"ok": True}


def test_204_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    sequence(monkeypatch, response(204))
    assert api_client.request("DELETE", "/x") is None


def test_app_error_uses_backend_message_and_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"error": {"code": "service_unavailable", "message": "The AI model is rate-limited."}}
    calls = sequence(monkeypatch, response(503, body))
    with pytest.raises(api_client.APIError) as info:
        api_client.request("POST", "/x")
    assert info.value.message == "The AI model is rate-limited."
    assert info.value.status_code == 503
    assert len(calls) == 1  # our own 503 is not a cold start


def test_cold_start_retries_until_awake(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = sequence(
        monkeypatch,
        httpx.ConnectError("refused"),
        response(502, text="<html>Bad Gateway</html>"),  # Render proxy while waking
        response(200, {"ok": True}),
    )
    assert api_client.request("GET", "/health") == {"ok": True}
    assert len(calls) == 3


def test_cold_start_gives_up_with_friendly_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_client, "COLD_START_MAX_WAIT", 0.0)
    sequence(monkeypatch, httpx.ConnectError("refused"))
    with pytest.raises(api_client.APIError, match="Cannot reach the server"):
        api_client.request("GET", "/health")


def test_read_timeout_is_friendly(monkeypatch: pytest.MonkeyPatch) -> None:
    sequence(monkeypatch, httpx.ReadTimeout("slow"))
    with pytest.raises(api_client.APIError, match="took too long"):
        api_client.request("POST", "/x")


def test_auth_header_attached_when_token_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_client.st, "session_state", {api_client.TOKEN_KEY: "jwt-123"})
    calls = sequence(monkeypatch, response(200, {}))
    api_client.request("GET", "/x")
    assert calls[0]["headers"] == {"Authorization": "Bearer jwt-123"}


def test_long_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = sequence(monkeypatch, response(201, {"id": "q"}))
    api_client.create_quiz({})
    assert calls[0]["timeout"] >= 240
