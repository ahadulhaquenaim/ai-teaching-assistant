"""Repositories for `chat_sessions` and `chat_messages`.

Messages live in their own collection (never an unbounded array on the
session). Session reads are filtered by `user_id`, so a session owned by
someone else behaves exactly like a missing one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pymongo import ASCENDING, DESCENDING

from app.db.repositories.documents import MongoDoc, parse_object_id
from app.schemas.chat import ChatSessionOut, MessageOut, Role

DEFAULT_SESSION_TITLE = "New chat"


def to_session_out(doc: MongoDoc) -> ChatSessionOut:
    return ChatSessionOut(
        id=str(doc["_id"]),
        document_id=doc["document_id"],
        title=doc["title"],
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


def to_message_out(doc: MongoDoc) -> MessageOut:
    return MessageOut(
        id=str(doc["_id"]),
        session_id=doc["session_id"],
        role=doc["role"],
        content=doc["content"],
        web_search_enabled=doc.get("web_search_enabled", False),
        sources=doc.get("sources", []),
        created_at=doc["created_at"],
    )


class ChatSessionRepository:
    def __init__(self, db: Any) -> None:
        self._col = db["chat_sessions"]
        self._messages = db["chat_messages"]

    async def create(self, user_id: str, document_id: str) -> MongoDoc:
        now = datetime.now(UTC)
        doc: MongoDoc = {
            "user_id": user_id,
            "document_id": document_id,
            "title": DEFAULT_SESSION_TITLE,
            "created_at": now,
            "updated_at": now,
        }
        result = await self._col.insert_one(doc)
        doc["_id"] = result.inserted_id
        return doc

    async def get_owned(self, session_id: str, user_id: str) -> MongoDoc | None:
        oid = parse_object_id(session_id)
        if oid is None:
            return None
        return await self._col.find_one({"_id": oid, "user_id": user_id})

    async def list_for_user(self, user_id: str, document_id: str | None = None) -> list[MongoDoc]:
        query: MongoDoc = {"user_id": user_id}
        if document_id is not None:
            query["document_id"] = document_id
        return await self._col.find(query).sort("updated_at", DESCENDING).to_list(length=None)

    async def touch(self, session_id: Any, title: str | None = None) -> None:
        """Bump `updated_at`; optionally set the title (first question)."""
        update: MongoDoc = {"updated_at": datetime.now(UTC)}
        if title is not None:
            update["title"] = title
        await self._col.update_one({"_id": session_id}, {"$set": update})

    async def delete(self, session_id: str, user_id: str) -> bool:
        doc = await self.get_owned(session_id, user_id)
        if doc is None:
            return False
        await self._messages.delete_many({"session_id": str(doc["_id"])})
        await self._col.delete_one({"_id": doc["_id"], "user_id": user_id})
        return True


class ChatMessageRepository:
    def __init__(self, db: Any) -> None:
        self._col = db["chat_messages"]

    async def add(
        self,
        *,
        session_id: str,
        user_id: str,
        role: Role,
        content: str,
        web_search_enabled: bool,
        sources: list[dict[str, Any]],
    ) -> MongoDoc:
        doc: MongoDoc = {
            "session_id": session_id,
            "user_id": user_id,
            "role": role.value,
            "content": content,
            "web_search_enabled": web_search_enabled,
            "sources": sources,
            "created_at": datetime.now(UTC),
        }
        result = await self._col.insert_one(doc)
        doc["_id"] = result.inserted_id
        return doc

    async def list(self, session_id: str) -> list[MongoDoc]:
        return await self._col.find({"session_id": session_id}).sort(
            [("created_at", ASCENDING), ("_id", ASCENDING)]
        ).to_list(length=None)

    async def recent(self, session_id: str, limit: int) -> list[MongoDoc]:
        """The last `limit` messages, oldest first."""
        docs = await self._col.find({"session_id": session_id}).sort(
            [("created_at", DESCENDING), ("_id", DESCENDING)]
        ).limit(limit).to_list(length=None)
        return list(reversed(docs))
