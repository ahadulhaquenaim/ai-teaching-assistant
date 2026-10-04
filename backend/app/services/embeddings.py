"""Gemini embedding service.

Documents and queries use different Gemini task types, which improves
retrieval quality. Embedding calls are batched and retried with exponential
backoff because the free tier rate-limits per minute.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Protocol

from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app.config import Settings
from app.services.retry import with_retry

logger = logging.getLogger(__name__)

_DOCUMENT_TASK = "RETRIEVAL_DOCUMENT"
_QUERY_TASK = "RETRIEVAL_QUERY"


class Embedder(Protocol):
    """Interface used by ingestion and retrieval (lets tests inject fakes)."""

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class GeminiEmbeddingService:
    """Embeds text with the configured Gemini model and output dimension."""

    def __init__(self, settings: Settings) -> None:
        if settings.google_api_key is None:
            raise ValueError("GOOGLE_API_KEY is required for Gemini embeddings")
        self._dimension = settings.embedding_dimension
        self._batch_size = settings.embedding_batch_size
        self._batch_delay = settings.embedding_batch_delay_seconds
        self._model = GoogleGenerativeAIEmbeddings(
            model=settings.embedding_model,
            api_key=settings.google_api_key,
            output_dimensionality=settings.embedding_dimension,
        )

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts in batches; returns one vector per input, in order."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            batch_vectors = await with_retry(
                lambda b=batch: self._model.aembed_documents(  # type: ignore[misc]
                    b, batch_size=len(b), task_type=_DOCUMENT_TASK
                )
            )
            self._check_dimensions(batch_vectors)
            vectors.extend(batch_vectors)
            # Space out batches to stay under the free-tier per-minute limit.
            if self._batch_delay and start + self._batch_size < len(texts):
                await asyncio.sleep(self._batch_delay)
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        vector = await with_retry(
            lambda: self._model.aembed_query(text, task_type=_QUERY_TASK)
        )
        self._check_dimensions([vector])
        return vector

    def _check_dimensions(self, vectors: list[list[float]]) -> None:
        # A mismatch here means EMBEDDING_DIMENSION and the Pinecone index disagree.
        for vector in vectors:
            if len(vector) != self._dimension:
                raise ValueError(
                    f"Embedding has {len(vector)} dimensions, expected {self._dimension}"
                )
