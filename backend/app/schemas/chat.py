"""Pydantic schemas for chat sessions, messages, and typed sources."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class DocumentSource(BaseModel):
    type: Literal["document"] = "document"
    page: int = Field(ge=1)


class WebSource(BaseModel):
    type: Literal["web"] = "web"
    title: str
    url: str


Source = Annotated[DocumentSource | WebSource, Field(discriminator="type")]


# ------------------------------------------------------------------ requests
class ChatSessionCreate(BaseModel):
    document_id: str = Field(min_length=1)


class MessageCreate(BaseModel):
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    web_search: bool = False


# ----------------------------------------------------------------- responses
class ChatSessionOut(BaseModel):
    id: str
    document_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ChatSessionListResponse(BaseModel):
    sessions: list[ChatSessionOut]


class MessageOut(BaseModel):
    id: str
    session_id: str
    role: Role
    content: str
    web_search_enabled: bool
    sources: list[Source]
    created_at: datetime


class MessageListResponse(BaseModel):
    messages: list[MessageOut]


class ChatTurnResponse(BaseModel):
    """Result of `POST /chat/sessions/{id}/messages`."""

    user_message: MessageOut
    assistant_message: MessageOut
