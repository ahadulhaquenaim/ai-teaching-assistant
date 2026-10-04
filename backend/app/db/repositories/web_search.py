"""Repositories for `web_search_usage` (daily limits) and `web_search_cache`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


def today_utc() -> str:
    """Usage is counted per UTC calendar day."""
    return datetime.now(UTC).strftime("%Y-%m-%d")


class WebSearchUsageRepository:
    def __init__(self, db: Any) -> None:
        self._col = db["web_search_usage"]

    async def try_consume(self, user_id: str, limit: int) -> bool:
        """Atomically take one search from today's allowance.

        The filter only matches while `count < limit`. When the user is at the
        limit the filter misses, the upsert tries to insert a second doc for
        (user_id, date), and the unique index rejects it -> limit reached.
        Concurrent requests therefore cannot exceed the limit.
        """
        if limit <= 0:
            return False
        try:
            await self._col.find_one_and_update(
                {"user_id": user_id, "date": today_utc(), "count": {"$lt": limit}},
                {"$inc": {"count": 1}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            return True
        except DuplicateKeyError:
            return False

    async def refund(self, user_id: str) -> None:
        """Give back a search that failed (the user should not pay for our outage)."""
        await self._col.update_one(
            {"user_id": user_id, "date": today_utc(), "count": {"$gt": 0}},
            {"$inc": {"count": -1}},
        )

    async def used_today(self, user_id: str) -> int:
        doc = await self._col.find_one({"user_id": user_id, "date": today_utc()})
        return int(doc["count"]) if doc else 0


class WebSearchCacheRepository:
    """24h cache keyed by query hash (TTL index on `created_at`)."""

    def __init__(self, db: Any) -> None:
        self._col = db["web_search_cache"]

    async def get(self, query_hash: str) -> list[dict[str, Any]] | None:
        doc = await self._col.find_one({"query_hash": query_hash})
        return doc["results"] if doc else None

    async def set(self, query_hash: str, results: list[dict[str, Any]]) -> None:
        await self._col.update_one(
            {"query_hash": query_hash},
            {"$set": {"results": results, "created_at": datetime.now(UTC)}},
            upsert=True,
        )
