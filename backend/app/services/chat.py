"""Chat use-cases: sessions, history, and running the Q&A graph for a message."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.core.errors import ConflictError, NotFoundError, ServiceUnavailableError
from app.db.repositories.chat import (
    DEFAULT_SESSION_TITLE,
    ChatMessageRepository,
    ChatSessionRepository,
    MongoDoc,
)
from app.db.repositories.documents import DocumentRepository
from app.graphs.qa_graph import ChatTurn, QAGraph
from app.schemas.chat import Role
from app.schemas.document import DocumentStatus
from app.services.llm import LLMError, LLMRateLimitError

logger = logging.getLogger(__name__)

_TITLE_MAX_CHARS = 60


def make_title(question: str) -> str:
    """Session title from the first question: one line, cut at a word boundary."""
    text = re.sub(r"\s+", " ", question).strip()
    if len(text) <= _TITLE_MAX_CHARS:
        return text
    cut = text[:_TITLE_MAX_CHARS].rsplit(" ", 1)[0] or text[:_TITLE_MAX_CHARS]
    return cut.rstrip(" ,.;:") + "…"


class ChatService:
    def __init__(
        self,
        sessions: ChatSessionRepository,
        messages: ChatMessageRepository,
        documents: DocumentRepository,
        qa_graph: QAGraph,
        *,
        history_messages: int,
    ) -> None:
        self._sessions = sessions
        self._messages = messages
        self._documents = documents
        self._graph = qa_graph
        self._history_messages = history_messages

    async def _ready_document(self, document_id: str, user_id: str) -> MongoDoc:
        """Ownership check + readiness check for the document behind a chat."""
        doc = await self._documents.get_owned(document_id, user_id)
        if doc is None:
            raise NotFoundError("Document not found.")
        if doc["status"] != DocumentStatus.READY.value:
            raise ConflictError("The document is not ready for chat yet.")
        return doc

    async def _owned_session(self, session_id: str, user_id: str) -> MongoDoc:
        session = await self._sessions.get_owned(session_id, user_id)
        if session is None:
            raise NotFoundError("Chat session not found.")
        return session

    # ------------------------------------------------------------ sessions
    async def create_session(self, user_id: str, document_id: str) -> MongoDoc:
        await self._ready_document(document_id, user_id)
        return await self._sessions.create(user_id, document_id)

    async def list_sessions(self, user_id: str, document_id: str | None) -> list[MongoDoc]:
        return await self._sessions.list_for_user(user_id, document_id)

    async def delete_session(self, user_id: str, session_id: str) -> None:
        if not await self._sessions.delete(session_id, user_id):
            raise NotFoundError("Chat session not found.")

    # ------------------------------------------------------------ messages
    async def list_messages(self, user_id: str, session_id: str) -> list[MongoDoc]:
        await self._owned_session(session_id, user_id)
        return await self._messages.list(session_id)

    async def send_message(
        self, user_id: str, session_id: str, content: str, web_search: bool
    ) -> tuple[MongoDoc, MongoDoc]:
        """Answer a question in a session; returns (user_message, assistant_message)."""
        session = await self._owned_session(session_id, user_id)
        # The document id comes from our DB, never from the client.
        document = await self._ready_document(session["document_id"], user_id)

        recent = await self._messages.recent(session_id, self._history_messages)
        history: list[ChatTurn] = [{"role": m["role"], "content": m["content"]} for m in recent]

        try:
            result = await self._graph.run(
                question=content,
                chat_history=history,
                document_id=session["document_id"],
                document_summary=document.get("summary"),
                web_search_enabled=web_search,
                user_id=user_id,
            )
        except LLMRateLimitError as exc:
            raise ServiceUnavailableError(str(exc)) from exc
        except LLMError as exc:
            raise ServiceUnavailableError("The AI model is temporarily unavailable. Please try again.") from exc
        except Exception as exc:
            # Embedding/Pinecone failures after retries.
            logger.exception("qa graph failed", extra={"session_id": session_id})
            raise ServiceUnavailableError(
                "The assistant is temporarily unavailable. Please try again."
            ) from exc

        # Persist only after a successful answer, so a failed turn can be retried cleanly.
        user_msg = await self._messages.add(
            session_id=session_id,
            user_id=user_id,
            role=Role.USER,
            content=content,
            web_search_enabled=web_search,
            sources=[],
        )
        sources: list[dict[str, Any]] = result.sources
        assistant_msg = await self._messages.add(
            session_id=session_id,
            user_id=user_id,
            role=Role.ASSISTANT,
            content=result.answer,
            web_search_enabled=web_search,
            sources=sources,
        )
        title = make_title(content) if session["title"] == DEFAULT_SESSION_TITLE else None
        await self._sessions.touch(session["_id"], title=title)
        return user_msg, assistant_msg
