"""Document use-cases: validated upload, listing, ownership-checked access, delete."""

from __future__ import annotations

import io
import logging
import os
import zipfile
from dataclasses import dataclass

from fastapi import UploadFile

from app.config import Settings
from app.core.errors import (
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    ServiceUnavailableError,
    UnsupportedMediaTypeError,
)
from app.db.repositories.documents import DocumentRepository, MongoDoc
from app.schemas.document import DocumentStatus, FileType
from app.services.vectorstore import VectorStore

logger = logging.getLogger(__name__)

_READ_CHUNK = 1024 * 1024  # 1 MB
_EXTENSIONS = {".pdf": FileType.PDF, ".docx": FileType.DOCX}


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    file_type: FileType
    data: bytes


def sanitize_filename(filename: str | None) -> str:
    """Keep only the base name (no client paths) and cap its length."""
    name = os.path.basename((filename or "").replace("\\", "/")).strip()
    return name[:255] or "document"


def detect_file_type(filename: str, data: bytes) -> FileType:
    """Validate by extension AND file content; the client's Content-Type is not trusted."""
    ext = os.path.splitext(filename)[1].lower()
    file_type = _EXTENSIONS.get(ext)
    if file_type is None:
        raise UnsupportedMediaTypeError("Only PDF and DOCX files are supported.")

    if file_type is FileType.PDF and not data.startswith(b"%PDF-"):
        raise UnsupportedMediaTypeError("The file is not a valid PDF.")
    if file_type is FileType.DOCX:
        if not data.startswith(b"PK\x03\x04"):
            raise UnsupportedMediaTypeError("The file is not a valid DOCX document.")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                if "word/document.xml" not in zf.namelist():
                    raise UnsupportedMediaTypeError("The file is not a valid DOCX document.")
        except zipfile.BadZipFile as exc:
            raise UnsupportedMediaTypeError("The file is not a valid DOCX document.") from exc
    return file_type


async def read_limited(upload: UploadFile, max_bytes: int) -> bytes:
    """Read the upload, aborting as soon as it exceeds `max_bytes`."""
    buffer = bytearray()
    while chunk := await upload.read(_READ_CHUNK):
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise PayloadTooLargeError(
                f"File is too large. The maximum size is {max_bytes // (1024 * 1024)} MB."
            )
    if not buffer:
        raise UnsupportedMediaTypeError("The uploaded file is empty.")
    return bytes(buffer)


class DocumentService:
    def __init__(
        self, settings: Settings, repo: DocumentRepository, vector_store: VectorStore
    ) -> None:
        self._settings = settings
        self._repo = repo
        self._vectors = vector_store

    async def validate_upload(self, upload: UploadFile) -> ValidatedUpload:
        filename = sanitize_filename(upload.filename)
        # Reject by extension before reading anything large.
        if os.path.splitext(filename)[1].lower() not in _EXTENSIONS:
            raise UnsupportedMediaTypeError("Only PDF and DOCX files are supported.")
        data = await read_limited(upload, self._settings.max_upload_size_bytes)
        return ValidatedUpload(filename, detect_file_type(filename, data), data)

    async def create(self, owner_id: str, upload: ValidatedUpload) -> str:
        """Check quotas and create the document record in `processing` state."""
        if await self._repo.count_active(owner_id) >= self._settings.max_documents_per_user:
            raise ConflictError(
                f"You can have at most {self._settings.max_documents_per_user} documents. "
                "Delete one to upload another."
            )
        if await self._repo.count_active() >= self._settings.max_documents_total:
            raise ConflictError(
                "The app has reached its document capacity (free-tier limit). "
                "Please try again later."
            )
        document_id = await self._repo.create(owner_id, upload.filename, upload.file_type)
        logger.info(
            "document created",
            extra={"document_id": document_id, "file_type": upload.file_type.value},
        )
        return document_id

    async def get(self, owner_id: str, document_id: str) -> MongoDoc:
        doc = await self._repo.get_owned(document_id, owner_id)
        if doc is None:
            raise NotFoundError("Document not found.")
        return doc

    async def list(self, owner_id: str) -> list[MongoDoc]:
        return await self._repo.list_for_owner(owner_id)

    async def delete(self, owner_id: str, document_id: str) -> None:
        """Delete vectors first, then Mongo data, so nothing is left orphaned."""
        doc = await self.get(owner_id, document_id)  # ownership check
        if doc["status"] == DocumentStatus.PROCESSING.value:
            # The background task would keep upserting into a deleted namespace.
            raise ConflictError("The document is still processing. Try again when it finishes.")
        try:
            await self._vectors.delete_document(document_id)
        except Exception as exc:
            logger.exception("pinecone delete failed", extra={"document_id": document_id})
            raise ServiceUnavailableError(
                "Could not delete the document's search data. Please try again."
            ) from exc
        await self._repo.delete_cascade(document_id, owner_id)
