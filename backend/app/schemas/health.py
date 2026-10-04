"""Health-check response schema."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    mongo: Literal["ok", "unavailable"]
    version: str
