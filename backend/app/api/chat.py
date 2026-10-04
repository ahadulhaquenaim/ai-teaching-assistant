"""Chat endpoints: sessions and grounded Q&A messages."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.dependencies import CurrentUserDep, get_chat_service
from app.db.repositories.chat import to_message_out, to_session_out
from app.schemas.chat import (
    ChatSessionCreate,
    ChatSessionListResponse,
    ChatSessionOut,
    ChatTurnResponse,
    MessageCreate,
    MessageListResponse,
)
from app.services.chat import ChatService

router = APIRouter(prefix="/chat", tags=["chat"])

ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]


@router.post("/sessions", status_code=status.HTTP_201_CREATED, response_model=ChatSessionOut)
async def create_session(
    body: ChatSessionCreate, user: CurrentUserDep, service: ChatServiceDep
) -> ChatSessionOut:
    """Start a chat about one of the user's ready documents."""
    return to_session_out(await service.create_session(user.user_id, body.document_id))


@router.get("/sessions", response_model=ChatSessionListResponse)
async def list_sessions(
    user: CurrentUserDep,
    service: ChatServiceDep,
    document_id: Annotated[str | None, Query()] = None,
) -> ChatSessionListResponse:
    sessions = await service.list_sessions(user.user_id, document_id)
    return ChatSessionListResponse(sessions=[to_session_out(s) for s in sessions])


@router.get("/sessions/{session_id}/messages", response_model=MessageListResponse)
async def list_messages(
    session_id: str, user: CurrentUserDep, service: ChatServiceDep
) -> MessageListResponse:
    messages = await service.list_messages(user.user_id, session_id)
    return MessageListResponse(messages=[to_message_out(m) for m in messages])


@router.post("/sessions/{session_id}/messages", response_model=ChatTurnResponse)
async def send_message(
    session_id: str, body: MessageCreate, user: CurrentUserDep, service: ChatServiceDep
) -> ChatTurnResponse:
    """Ask a question. The answer is grounded in the document with page citations."""
    user_msg, assistant_msg = await service.send_message(
        user.user_id, session_id, body.content.strip(), body.web_search
    )
    return ChatTurnResponse(
        user_message=to_message_out(user_msg), assistant_message=to_message_out(assistant_msg)
    )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: str, user: CurrentUserDep, service: ChatServiceDep) -> None:
    await service.delete_session(user.user_id, session_id)
