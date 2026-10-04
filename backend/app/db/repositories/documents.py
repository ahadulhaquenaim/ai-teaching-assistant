"""Repository for the `documents` collection.

Every read that returns a single document for an API request goes through
`get_owned`, which filters on BOTH `_id` and `owner_id`. A document that
exists but belongs to someone else is indistinguishable from one that does
not exist (both return None -> HTTP 404), so ids cannot be probed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import DESCENDING

from app.schemas.document import DocumentOut, DocumentStatus, FileType

logger = logging.getLogger(__name__)

MongoDoc = dict[str, Any]

# Statuses that occupy a Pinecone namespace slot (failed docs are cleaned up).
_ACTIVE_STATUSES = [DocumentStatus.PROCESSING.value, DocumentStatus.READY.value]


def parse_object_id(value: str) -> ObjectId | None:
    """Parse a client-supplied id; invalid ids are treated as not found."""
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def to_document_out(doc: MongoDoc) -> DocumentOut:
    return DocumentOut(
        id=str(doc["_id"]),
        owner_id=doc["owner_id"],
        filename=doc["filename"],
        file_type=doc["file_type"],
        status=doc["status"],
        page_count=doc.get("page_count"),
        chunk_count=doc.get("chunk_count"),
        summary=doc.get("summary"),
        error_message=doc.get("error_message"),
        created_at=doc["created_at"],
    )


class DocumentRepository:
    def __init__(self, db: Any) -> None:
        self._db = db
        self._col = db["documents"]

    async def create(self, owner_id: str, filename: str, file_type: FileType) -> str:
        doc: MongoDoc = {
            "owner_id": owner_id,
            "filename": filename,
            "file_type": file_type.value,
            "status": DocumentStatus.PROCESSING.value,
            "page_count": None,
            "chunk_count": None,
            "summary": None,
            "error_message": None,
            "created_at": datetime.now(UTC),
        }
        result = await self._col.insert_one(doc)
        return str(result.inserted_id)

    async def get_owned(self, document_id: str, owner_id: str) -> MongoDoc | None:
        """Return the document only if it exists AND belongs to `owner_id`."""
        oid = parse_object_id(document_id)
        if oid is None:
            return None
        return await self._col.find_one({"_id": oid, "owner_id": owner_id})

    async def list_for_owner(self, owner_id: str) -> list[MongoDoc]:
        cursor = self._col.find({"owner_id": owner_id}).sort("created_at", DESCENDING)
        return await cursor.to_list(length=None)

    async def count_active(self, owner_id: str | None = None) -> int:
        """Count documents holding a Pinecone namespace (optionally for one owner)."""
        query: MongoDoc = {"status": {"$in": _ACTIVE_STATUSES}}
        if owner_id is not None:
            query["owner_id"] = owner_id
        return await self._col.count_documents(query)

    async def mark_ready(
        self, document_id: str, *, page_count: int, chunk_count: int, summary: str | None
    ) -> None:
        await self._col.update_one(
            {"_id": ObjectId(document_id)},
            {
                "$set": {
                    "status": DocumentStatus.READY.value,
                    "page_count": page_count,
                    "chunk_count": chunk_count,
                    "summary": summary,
                    "error_message": None,
                }
            },
        )

    async def mark_failed(self, document_id: str, error_message: str) -> None:
        await self._col.update_one(
            {"_id": ObjectId(document_id)},
            {"$set": {"status": DocumentStatus.FAILED.value, "error_message": error_message}},
        )

    async def fail_interrupted(self) -> list[str]:
        """Mark every `processing` document as failed and return their ids.

        Called once at startup. Ingestion runs in-process (BackgroundTasks), so
        after a restart/sleep on Render nothing is working on these anymore.
        Assumes a single worker process, which is how the free tier runs.
        """
        stuck = await self._col.find(
            {"status": DocumentStatus.PROCESSING.value}, {"_id": 1}
        ).to_list(length=None)
        ids = [str(d["_id"]) for d in stuck]
        if ids:
            await self._col.update_many(
                {"_id": {"$in": [d["_id"] for d in stuck]}},
                {
                    "$set": {
                        "status": DocumentStatus.FAILED.value,
                        "error_message": "Processing was interrupted by a server restart. "
                        "Please delete this document and upload it again.",
                    }
                },
            )
            logger.warning("marked interrupted documents as failed", extra={"count": len(ids)})
        return ids

    async def delete_cascade(self, document_id: str, owner_id: str) -> bool:
        """Delete a document and all chat/quiz data that references it.

        Pinecone data is deleted by the caller (DocumentService) first.
        Returns False if the document does not exist or is not owned.
        """
        doc = await self.get_owned(document_id, owner_id)
        if doc is None:
            return False

        session_ids = [
            s["_id"]
            async for s in self._db["chat_sessions"].find({"document_id": document_id}, {"_id": 1})
        ]
        if session_ids:
            await self._db["chat_messages"].delete_many(
                {"session_id": {"$in": [str(i) for i in session_ids]}}
            )
            await self._db["chat_sessions"].delete_many({"_id": {"$in": session_ids}})

        quiz_ids = [
            q["_id"]
            async for q in self._db["quizzes"].find({"document_id": document_id}, {"_id": 1})
        ]
        if quiz_ids:
            await self._db["quiz_attempts"].delete_many(
                {"quiz_id": {"$in": [str(i) for i in quiz_ids]}}
            )
            await self._db["quizzes"].delete_many({"_id": {"$in": quiz_ids}})

        await self._col.delete_one({"_id": doc["_id"], "owner_id": owner_id})
        logger.info(
            "document deleted",
            extra={
                "document_id": document_id,
                "sessions": len(session_ids),
                "quizzes": len(quiz_ids),
            },
        )
        return True
