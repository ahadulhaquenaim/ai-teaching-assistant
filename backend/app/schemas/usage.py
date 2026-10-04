"""Schemas for usage endpoints."""

from __future__ import annotations

from pydantic import BaseModel


class WebSearchUsageOut(BaseModel):
    date: str  # UTC day, YYYY-MM-DD
    limit: int
    used: int
    remaining: int
