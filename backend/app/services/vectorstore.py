"""Pinecone vector store (async client).

One index; one namespace per document (`namespace = document_id`).

SECURITY: this layer does not know about users. Callers MUST verify that the
current user owns `document_id` before calling any method here; the
document services/graphs only pass ids that came from an ownership-checked
Mongo lookup.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from pinecone import PineconeAsyncio
from pinecone.exceptions import NotFoundException

from app.config import Settings
from app.services.retry import with_retry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChunkRecord:
    """One chunk of a document, ready to be stored."""

    chunk_index: int
    page_number: int
    text: str


@dataclass(frozen=True)
class RetrievedChunk:
    """A chunk returned by similarity search."""

    chunk_index: int
    page_number: int
    text: str
    score: float


def vector_id(document_id: str, chunk_index: int) -> str:
    """Deterministic vector id; lets re-ingestion overwrite and enables fetch-by-id."""
    return f"{document_id}#{chunk_index}"


class VectorStore(Protocol):
    async def upsert_chunks(
        self, document_id: str, chunks: Sequence[ChunkRecord], vectors: Sequence[list[float]]
    ) -> None: ...

    async def query(
        self, document_id: str, vector: list[float], top_k: int
    ) -> list[RetrievedChunk]: ...

    async def fetch_chunks(self, document_id: str, chunk_indices: Sequence[int]) -> list[RetrievedChunk]: ...

    async def delete_document(self, document_id: str) -> None: ...

    async def aclose(self) -> None: ...


class PineconeVectorStore:
    """Async Pinecone wrapper. The index connection is opened lazily on first use."""

    def __init__(self, settings: Settings) -> None:
        if settings.pinecone_api_key is None:
            raise ValueError("PINECONE_API_KEY is required")
        self._api_key = settings.pinecone_api_key
        self._index_name = settings.pinecone_index_name
        self._batch_size = settings.pinecone_upsert_batch_size
        self._client: PineconeAsyncio | None = None
        self._index: Any = None
        self._lock = asyncio.Lock()

    async def _get_index(self) -> Any:
        async with self._lock:
            if self._index is None:
                self._client = PineconeAsyncio(api_key=self._api_key.get_secret_value())
                description = await with_retry(
                    lambda: self._client.describe_index(self._index_name)  # type: ignore[union-attr]
                )
                self._index = self._client.IndexAsyncio(host=description.host)
                logger.info("pinecone index connected", extra={"index": self._index_name})
            return self._index

    async def upsert_chunks(
        self, document_id: str, chunks: Sequence[ChunkRecord], vectors: Sequence[list[float]]
    ) -> None:
        """Upsert chunk vectors into the document's namespace, in batches."""
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")
        index = await self._get_index()
        records = [
            {
                "id": vector_id(document_id, chunk.chunk_index),
                "values": values,
                "metadata": {
                    "document_id": document_id,
                    "page_number": chunk.page_number,
                    "chunk_index": chunk.chunk_index,
                    "text": chunk.text,
                },
            }
            for chunk, values in zip(chunks, vectors, strict=True)
        ]
        for start in range(0, len(records), self._batch_size):
            batch = records[start : start + self._batch_size]
            await with_retry(
                lambda b=batch: index.upsert(  # type: ignore[misc]
                    vectors=b, namespace=document_id, show_progress=False
                )
            )

    async def query(self, document_id: str, vector: list[float], top_k: int) -> list[RetrievedChunk]:
        """Similarity search restricted to one document's namespace."""
        index = await self._get_index()
        response = await with_retry(
            lambda: index.query(
                vector=vector, top_k=top_k, namespace=document_id, include_metadata=True
            )
        )
        results: list[RetrievedChunk] = []
        for match in response.matches or []:
            meta = match.metadata or {}
            # Defense in depth: never return a chunk from a different document.
            if meta.get("document_id") != document_id:
                continue
            results.append(
                RetrievedChunk(
                    chunk_index=int(meta.get("chunk_index", -1)),
                    page_number=int(meta.get("page_number", 0)),
                    text=str(meta.get("text", "")),
                    score=float(match.score or 0.0),
                )
            )
        return results

    async def fetch_chunks(self, document_id: str, chunk_indices: Sequence[int]) -> list[RetrievedChunk]:
        """Fetch specific chunks by index (used to sample across a whole document)."""
        if not chunk_indices:
            return []
        index = await self._get_index()
        ids = [vector_id(document_id, i) for i in chunk_indices]
        response = await with_retry(lambda: index.fetch(ids=ids, namespace=document_id))
        chunks: list[RetrievedChunk] = []
        for vector in (response.vectors or {}).values():
            meta = vector.metadata or {}
            if meta.get("document_id") != document_id:
                continue
            chunks.append(
                RetrievedChunk(
                    chunk_index=int(meta.get("chunk_index", -1)),
                    page_number=int(meta.get("page_number", 0)),
                    text=str(meta.get("text", "")),
                    score=1.0,
                )
            )
        return sorted(chunks, key=lambda c: c.chunk_index)

    async def delete_document(self, document_id: str) -> None:
        """Delete every vector of a document by dropping its namespace."""
        index = await self._get_index()
        try:
            await with_retry(lambda: index.delete_namespace(namespace=document_id))
            logger.info("pinecone namespace deleted", extra={"document_id": document_id})
        except NotFoundException:
            # Nothing was ever upserted (e.g. ingestion failed early).
            logger.info("pinecone namespace not found", extra={"document_id": document_id})

    async def aclose(self) -> None:
        if self._index is not None:
            await self._index.close()
            self._index = None
        if self._client is not None:
            await self._client.close()
            self._client = None
