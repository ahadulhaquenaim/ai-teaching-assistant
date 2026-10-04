"""Document ingestion: extract -> clean -> chunk -> embed -> upsert -> summarize.

Runs as a FastAPI background task after `POST /documents` has already
returned. The uploaded bytes live only in memory for the duration of the
task and are never written to permanent storage.

Pure functions (extraction, cleaning, chunking) are module-level so they can
be unit-tested without any external services.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import zipfile
from dataclasses import dataclass

import docx2txt
import pymupdf
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import Settings
from app.db.repositories.documents import DocumentRepository
from app.prompts.summary import SUMMARY_SYSTEM_PROMPT, SUMMARY_USER_TEMPLATE
from app.schemas.document import FileType
from app.services.embeddings import Embedder
from app.services.llm import LLM, LLMError
from app.services.vectorstore import ChunkRecord, VectorStore

logger = logging.getLogger(__name__)

# Embed + upsert this many chunks at a time, so a large document never holds
# all of its vectors in memory at once (Render free tier: 512 MB RAM).
_PIPELINE_BATCH = 100


# --------------------------------------------------------------------- errors
class IngestionError(Exception):
    """Expected failure; `str(exc)` is a user-facing message stored on the document."""


class ScannedPDFError(IngestionError):
    pass


class EmptyDocumentError(IngestionError):
    pass


class CorruptFileError(IngestionError):
    pass


# ----------------------------------------------------------------- extraction
@dataclass(frozen=True)
class PageText:
    page_number: int  # 1-based
    text: str


def clean_text(text: str) -> str:
    """Normalize extracted text while keeping paragraph breaks."""
    text = text.replace("\x00", "")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # re-join words hyphenated at line ends
    text = re.sub(r"[ \t\f\v ]+", " ", text)  # collapse horizontal whitespace
    text = re.sub(r" *\n *", "\n", text)  # trim spaces around newlines
    text = re.sub(r"\n{3,}", "\n\n", text)  # at most one blank line
    return text.strip()


def extract_pdf_pages(data: bytes) -> list[PageText]:
    """Extract cleaned text per page from a PDF (page numbers are 1-based)."""
    try:
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            if pdf.needs_pass:
                raise CorruptFileError("This PDF is password-protected. Please upload an unlocked file.")
            return [
                PageText(page_number=i + 1, text=clean_text(page.get_text("text")))
                for i, page in enumerate(pdf)
            ]
    except IngestionError:
        raise
    except Exception as exc:
        raise CorruptFileError("The PDF could not be read. It may be corrupted.") from exc


def extract_docx_pages(data: bytes, chars_per_page: int) -> list[PageText]:
    """Extract DOCX text and split it into page-sized sections.

    DOCX files have no fixed pages (layout depends on the viewer), so citations
    refer to approximate sections of `chars_per_page` characters, split on
    paragraph boundaries.
    """
    try:
        raw = docx2txt.process(io.BytesIO(data))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise CorruptFileError("The DOCX file could not be read. It may be corrupted.") from exc

    text = clean_text(raw or "")
    if not text:
        return []

    pages: list[PageText] = []
    current = ""
    for paragraph in text.split("\n"):
        if current and len(current) + len(paragraph) + 1 > chars_per_page:
            pages.append(PageText(len(pages) + 1, current.strip()))
            current = ""
        current += paragraph + "\n"
    if current.strip():
        pages.append(PageText(len(pages) + 1, current.strip()))
    return pages


def ensure_has_text(pages: list[PageText], file_type: FileType, min_avg_chars: int) -> None:
    """Fail clearly on scanned/empty documents (OCR is not supported)."""
    total = sum(len(p.text) for p in pages)
    if file_type is FileType.PDF:
        if not pages:
            raise EmptyDocumentError("The PDF has no pages.")
        if total / len(pages) < min_avg_chars:
            raise ScannedPDFError(
                "This PDF contains almost no extractable text. It looks like a scanned "
                "document (images of pages). Scanned PDFs/OCR are not supported; please "
                "upload a PDF with selectable text."
            )
    elif total == 0:
        raise EmptyDocumentError("The document contains no text.")


# ------------------------------------------------------------------- chunking
def chunk_pages(pages: list[PageText], chunk_size: int, chunk_overlap: int) -> list[ChunkRecord]:
    """Split each page separately so every chunk maps to exactly one page."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks: list[ChunkRecord] = []
    for page in pages:
        if not page.text:
            continue
        for piece in splitter.split_text(page.text):
            piece = piece.strip()
            if piece:
                chunks.append(ChunkRecord(len(chunks), page.page_number, piece))
    return chunks


def build_summary_excerpts(chunks: list[ChunkRecord], max_chars: int) -> str:
    """Pick chunks spread evenly across the document, up to `max_chars`."""
    if not chunks:
        return ""
    avg = max(1, sum(len(c.text) for c in chunks) // len(chunks))
    wanted = max(1, min(len(chunks), max_chars // avg))
    step = len(chunks) / wanted
    picked = [chunks[int(i * step)] for i in range(wanted)]
    out: list[str] = []
    used = 0
    for chunk in picked:
        piece = f"[page {chunk.page_number}] {chunk.text}"
        if used + len(piece) > max_chars:
            break
        out.append(piece)
        used += len(piece)
    return "\n\n".join(out)


# ------------------------------------------------------------------- pipeline
class IngestionService:
    def __init__(
        self,
        settings: Settings,
        repo: DocumentRepository,
        embedder: Embedder,
        vector_store: VectorStore,
        llm: LLM,
    ) -> None:
        self._settings = settings
        self._repo = repo
        self._embedder = embedder
        self._vectors = vector_store
        self._llm = llm

    async def run(self, document_id: str, filename: str, file_type: FileType, data: bytes) -> None:
        """Process one uploaded file. Never raises: failures are recorded on the document."""
        log_extra = {"document_id": document_id, "file_type": file_type.value}
        logger.info("ingestion started", extra={**log_extra, "bytes": len(data)})
        try:
            # Parsing is CPU-bound; run it off the event loop.
            pages = await asyncio.to_thread(self._extract, file_type, data)
            del data  # release the upload bytes as early as possible
            ensure_has_text(pages, file_type, self._settings.min_avg_chars_per_page)

            chunks = chunk_pages(pages, self._settings.chunk_size, self._settings.chunk_overlap)
            if not chunks:
                raise EmptyDocumentError("The document contains no text.")

            for start in range(0, len(chunks), _PIPELINE_BATCH):
                batch = chunks[start : start + _PIPELINE_BATCH]
                vectors = await self._embedder.embed_documents([c.text for c in batch])
                await self._vectors.upsert_chunks(document_id, batch, vectors)
                logger.info(
                    "ingestion progress",
                    extra={**log_extra, "done": start + len(batch), "total": len(chunks)},
                )

            summary = await self._summarize(filename, chunks)
            await self._repo.mark_ready(
                document_id, page_count=len(pages), chunk_count=len(chunks), summary=summary
            )
            logger.info(
                "ingestion finished",
                extra={**log_extra, "pages": len(pages), "chunks": len(chunks)},
            )
        except IngestionError as exc:
            logger.info("ingestion rejected", extra={**log_extra, "reason": type(exc).__name__})
            await self._fail(document_id, str(exc))
        except Exception:
            logger.exception("ingestion failed", extra=log_extra)
            await self._fail(
                document_id,
                "Processing failed due to an internal error or an external service limit. "
                "Please try uploading again later.",
            )

    def _extract(self, file_type: FileType, data: bytes) -> list[PageText]:
        if file_type is FileType.PDF:
            return extract_pdf_pages(data)
        return extract_docx_pages(data, self._settings.docx_chars_per_page)

    async def _summarize(self, filename: str, chunks: list[ChunkRecord]) -> str | None:
        """Generate the 2-3 sentence summary. A failure here does not fail ingestion."""
        excerpts = build_summary_excerpts(chunks, self._settings.summary_max_input_chars)
        messages = [
            SystemMessage(SUMMARY_SYSTEM_PROMPT),
            HumanMessage(SUMMARY_USER_TEMPLATE.format(filename=filename, excerpts=excerpts)),
        ]
        try:
            return await self._llm.generate_text(messages) or None
        except LLMError:
            logger.warning("summary generation failed; continuing without summary")
            return None

    async def _fail(self, document_id: str, message: str) -> None:
        """Record the failure and remove any vectors already upserted."""
        try:
            await self._vectors.delete_document(document_id)
        except Exception:
            logger.exception("cleanup of partial vectors failed", extra={"document_id": document_id})
        await self._repo.mark_failed(document_id, message)
