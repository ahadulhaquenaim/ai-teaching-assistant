"""MongoDB index definitions.

`create_index` is idempotent, so this runs safely on every startup. Indexes
are declared as data so they are easy to review in one place.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger(__name__)

# web_search_cache entries expire after 24 hours.
WEB_SEARCH_CACHE_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class IndexSpec:
    collection: str
    keys: list[tuple[str, int]]
    options: dict[str, Any] = field(default_factory=dict)


INDEXES: list[IndexSpec] = [
    # users: Google's `sub` claim is the unique identity.
    IndexSpec("users", [("google_id", ASCENDING)], {"unique": True}),
    # documents: list a user's documents newest first.
    IndexSpec("documents", [("owner_id", ASCENDING), ("created_at", DESCENDING)]),
    # documents: find documents stuck in "processing" after a restart.
    IndexSpec("documents", [("status", ASCENDING), ("created_at", ASCENDING)]),
    # chat_sessions: a user's sessions, most recently active first.
    IndexSpec("chat_sessions", [("user_id", ASCENDING), ("updated_at", DESCENDING)]),
    # chat_sessions: sessions of one document (sidebar + cascade delete).
    IndexSpec("chat_sessions", [("document_id", ASCENDING)]),
    # chat_messages: a session's messages in chronological order.
    IndexSpec("chat_messages", [("session_id", ASCENDING), ("created_at", ASCENDING)]),
    # quizzes: a user's quizzes for one document.
    IndexSpec("quizzes", [("user_id", ASCENDING), ("document_id", ASCENDING)]),
    # quiz_attempts: attempts of one quiz (results + cascade delete).
    IndexSpec("quiz_attempts", [("quiz_id", ASCENDING)]),
    # web_search_usage: one counter per user per day.
    IndexSpec("web_search_usage", [("user_id", ASCENDING), ("date", ASCENDING)], {"unique": True}),
    # web_search_cache: lookup by query hash, auto-expire after 24h.
    IndexSpec("web_search_cache", [("query_hash", ASCENDING)], {"unique": True}),
    IndexSpec(
        "web_search_cache",
        [("created_at", ASCENDING)],
        {"expireAfterSeconds": WEB_SEARCH_CACHE_TTL_SECONDS},
    ),
]


async def ensure_indexes(db: AsyncDatabase[Any]) -> None:
    """Create all indexes declared in `INDEXES`."""
    for spec in INDEXES:
        name = await db[spec.collection].create_index(spec.keys, **spec.options)
        logger.debug("index ensured", extra={"collection": spec.collection, "index": name})
    logger.info("mongo indexes ensured", extra={"count": len(INDEXES)})
