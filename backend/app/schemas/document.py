"""Pydantic schemas for the `documents` collection and its API responses."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class DocumentStatus(StrEnum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class FileType(StrEnum):
    PDF = "pdf"
    DOCX = "docx"


class DocumentOut(BaseModel):
    """Document as returned by the API (Mongo `_id` renamed to `id`)."""

    id: str
    owner_id: str
    filename: str
    file_type: FileType
    status: DocumentStatus
    page_count: int | None = None
    chunk_count: int | None = None
    summary: str | None = None
    error_message: str | None = None
    created_at: datetime


class DocumentUploadResponse(BaseModel):
    """Returned immediately by `POST /documents`; ingestion continues in the background."""

    id: str
    status: DocumentStatus = DocumentStatus.PROCESSING
    message: str = Field(default="Upload accepted. Processing in the background.")


class DocumentListResponse(BaseModel):
    documents: list[DocumentOut]
