"""Long-lived external service clients, created once per app.

Stored on `app.state.services`; tests pass a container of fakes to
`create_app` instead, so no real API is ever called.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.services.embeddings import Embedder, GeminiEmbeddingService
from app.services.llm import LLM, LLMService
from app.services.vectorstore import PineconeVectorStore, VectorStore
from app.services.web_search import DuckDuckGoProvider, SearchProvider


@dataclass
class Services:
    llm: LLM
    embedder: Embedder
    vector_store: VectorStore
    web_provider: SearchProvider

    async def aclose(self) -> None:
        await self.vector_store.aclose()


def build_services(settings: Settings) -> Services:
    return Services(
        llm=LLMService.from_settings(settings),
        embedder=GeminiEmbeddingService(settings),
        vector_store=PineconeVectorStore(settings),
        web_provider=DuckDuckGoProvider(settings),
    )
