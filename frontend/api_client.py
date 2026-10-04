"""Single gateway for all frontend -> backend HTTP calls.

- Attaches `Authorization: Bearer <jwt>` when a token is in the session
  (auth is deferred today; the hook is here so enabling it is a small change).
- On HTTP 401: clears the session and reruns the app (back to login).
- Raises `APIError` with the backend's friendly message for every error.
- Long timeouts: answers and quizzes can take 20-60 s on free-tier LLMs.
- Render cold starts: the free instance sleeps when idle and takes ~30-60 s
  to wake. Connection failures and proxy errors (502/503/504 without our
  JSON error body) are retried while showing "Waking up the server...".
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx
import streamlit as st

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 120.0  # seconds; quiz generation passes a longer one
COLD_START_MAX_WAIT = 120.0  # total seconds to keep retrying while Render wakes up
_COLD_START_STATUSES = {502, 503, 504}
TOKEN_KEY = "auth_token"


class APIError(Exception):
    """A request failed; `message` is safe to show to the user."""

    def __init__(self, message: str, status_code: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def backend_url() -> str:
    """BACKEND_URL from Streamlit secrets, then the environment, then localhost."""
    try:
        url = st.secrets.get("BACKEND_URL")
    except Exception:  # no secrets.toml
        url = None
    return (url or os.environ.get("BACKEND_URL") or "http://localhost:8000").rstrip("/")


def _headers() -> dict[str, str]:
    token = st.session_state.get(TOKEN_KEY)
    return {"Authorization": f"Bearer {token}"} if token else {}


def _is_app_error(response: httpx.Response) -> bool:
    """True if the body is our backend's JSON error envelope (not a proxy page)."""
    try:
        return "error" in response.json()
    except ValueError:
        return False


def _error_message(response: httpx.Response) -> tuple[str, str | None]:
    try:
        error = response.json().get("error", {})
        return error.get("message") or response.reason_phrase, error.get("code")
    except ValueError:
        return f"Server error ({response.status_code}).", None


def _handle_unauthorized() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.warning("Your session has expired. Please sign in again.")
    st.rerun()


def request(
    method: str,
    path: str,
    *,
    json: Any = None,
    params: dict[str, Any] | None = None,
    files: Any = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """Send a request; returns parsed JSON (or None for 204). Raises APIError."""
    url = f"{backend_url()}{path}"
    deadline = time.monotonic() + COLD_START_MAX_WAIT
    delay = 3.0
    waking = None

    try:
        while True:
            try:
                response = httpx.request(
                    method, url, json=json, params=params, files=files, headers=_headers(), timeout=timeout
                )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError) as exc:
                response = None
                failure: Exception | None = exc
            except httpx.TimeoutException as exc:
                raise APIError("The server took too long to respond. Please try again.") from exc
            else:
                failure = None

            cold = response is None or (
                response.status_code in _COLD_START_STATUSES and not _is_app_error(response)
            )
            if not cold:
                break
            if time.monotonic() + delay > deadline:
                if failure is not None:
                    raise APIError("Cannot reach the server. Please try again in a minute.") from failure
                break
            if waking is None:
                waking = st.empty()
                waking.info("⏳ Waking up the server... (free hosting sleeps when idle, this can take up to a minute)")
            time.sleep(delay)
            delay = min(delay * 1.5, 10.0)
    finally:
        if waking is not None:
            waking.empty()

    assert response is not None
    if response.status_code == 401:
        _handle_unauthorized()
    if response.status_code >= 400:
        message, code = _error_message(response)
        raise APIError(message, response.status_code, code)
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


# ------------------------------------------------------------------ endpoints
def health() -> dict[str, Any]:
    return request("GET", "/health")


def list_documents() -> list[dict[str, Any]]:
    return request("GET", "/documents")["documents"]


def get_document(document_id: str) -> dict[str, Any]:
    return request("GET", f"/documents/{document_id}")


def upload_document(filename: str, data: bytes, content_type: str) -> dict[str, Any]:
    return request("POST", "/documents", files={"file": (filename, data, content_type)}, timeout=180)


def delete_document(document_id: str) -> None:
    request("DELETE", f"/documents/{document_id}")


def list_sessions(document_id: str | None = None) -> list[dict[str, Any]]:
    params = {"document_id": document_id} if document_id else None
    return request("GET", "/chat/sessions", params=params)["sessions"]


def create_session(document_id: str) -> dict[str, Any]:
    return request("POST", "/chat/sessions", json={"document_id": document_id})


def delete_session(session_id: str) -> None:
    request("DELETE", f"/chat/sessions/{session_id}")


def list_messages(session_id: str) -> list[dict[str, Any]]:
    return request("GET", f"/chat/sessions/{session_id}/messages")["messages"]


def send_message(session_id: str, content: str, web_search: bool) -> dict[str, Any]:
    return request(
        "POST",
        f"/chat/sessions/{session_id}/messages",
        json={"content": content, "web_search": web_search},
        timeout=240,
    )


def web_search_usage() -> dict[str, Any]:
    return request("GET", "/usage/web-search")


def create_quiz(payload: dict[str, Any]) -> dict[str, Any]:
    return request("POST", "/quizzes", json=payload, timeout=300)


def list_quizzes(document_id: str | None = None) -> list[dict[str, Any]]:
    params = {"document_id": document_id} if document_id else None
    return request("GET", "/quizzes", params=params)["quizzes"]


def get_quiz(quiz_id: str) -> dict[str, Any]:
    return request("GET", f"/quizzes/{quiz_id}")


def submit_quiz(quiz_id: str, answers: list[dict[str, Any]]) -> dict[str, Any]:
    return request("POST", f"/quizzes/{quiz_id}/submit", json={"answers": answers}, timeout=240)
