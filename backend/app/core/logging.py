"""Structured JSON logging and request-id tracking.

Every log line is a single JSON object, which Render's log viewer displays well
and which is easy to search. A per-request id is stored in a context variable,
attached to every log record, and returned in the `X-Request-ID` header.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes present on every LogRecord; anything else was passed via `extra=`.
_RESERVED_ATTRS = set(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
    "color_message",  # uvicorn's ANSI-colored duplicate of msg
}


class JsonFormatter(logging.Formatter):
    """Format log records as one-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if request_id := request_id_ctx.get():
            payload["request_id"] = request_id
        # Include structured fields passed with `logger.info(..., extra={...})`.
        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _add_request_id(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    event_dict.pop("color_message", None)  # uvicorn's ANSI duplicate of msg
    if request_id := request_id_ctx.get():
        # Short id is enough to correlate lines in a terminal.
        event_dict["req"] = request_id[:8]
    return event_dict


def _pretty_formatter() -> logging.Formatter:
    """Colored, human-readable lines for local development (structlog)."""
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="%H:%M:%S", utc=False),
            # Fields passed with `logger.info(..., extra={...})`.
            structlog.stdlib.ExtraAdder(),
            _add_request_id,
        ],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty()),
        ],
    )


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Configure the root logger to write to stdout.

    `fmt="json"` writes one JSON object per line; `fmt="pretty"` writes
    colored, readable lines for local development.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_pretty_formatter() if fmt == "pretty" else JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Route uvicorn's loggers through our JSON handler; drop its access log
    # because RequestLoggingMiddleware logs requests with more context.
    for name in ("uvicorn", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    # Keep chatty third-party loggers quiet.
    logging.getLogger("pymongo").setLevel(logging.WARNING)
    # google-genai warns about automatic function calling on every LLM call;
    # LangChain controls that, so the warning is noise.
    logging.getLogger("google_genai.models").setLevel(logging.ERROR)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Assign a request id and log one line per request with its duration."""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self._logger = logging.getLogger("app.request")

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
            self._logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                },
            )
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception:
            self._logger.exception(
                "request failed",
                extra={"method": request.method, "path": request.url.path},
            )
            raise
        finally:
            request_id_ctx.reset(token)
