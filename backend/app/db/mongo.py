"""MongoDB connection management using PyMongo's native async API.

One `AsyncMongoClient` is created for the app's lifetime (it manages its own
connection pool) and stored on `app.state`. Request handlers get the database
through the `get_db` dependency, so tests can swap it out easily.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from app.config import Settings

logger = logging.getLogger(__name__)

MongoDocument = dict[str, Any]


class MongoManager:
    """Owns the async Mongo client and exposes the app database."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: AsyncMongoClient[MongoDocument] | None = None

    async def connect(self) -> None:
        """Create the client. PyMongo connects lazily on first operation."""
        if self._client is not None:
            return
        self._client = AsyncMongoClient(
            self._settings.mongodb_uri.get_secret_value(),
            serverSelectionTimeoutMS=self._settings.mongodb_timeout_ms,
            connectTimeoutMS=self._settings.mongodb_timeout_ms,
            appname="ai-teaching-assistant",
            tz_aware=True,
        )
        logger.info("mongo client created", extra={"db": self._settings.mongodb_db_name})

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            logger.info("mongo client closed")

    @property
    def db(self) -> AsyncDatabase[MongoDocument]:
        if self._client is None:
            raise RuntimeError("MongoManager.connect() has not been called")
        return self._client[self._settings.mongodb_db_name]

    async def ping(self) -> bool:
        """Return True if MongoDB answers a ping, False otherwise."""
        try:
            await self.db.command("ping")
            return True
        except (PyMongoError, RuntimeError):
            logger.warning("mongo ping failed", exc_info=True)
            return False


def get_mongo(request: Request) -> MongoManager:
    """FastAPI dependency returning the app's MongoManager."""
    mongo: MongoManager = request.app.state.mongo
    return mongo


def get_db(request: Request) -> AsyncDatabase[MongoDocument]:
    """FastAPI dependency returning the app database."""
    return get_mongo(request).db
