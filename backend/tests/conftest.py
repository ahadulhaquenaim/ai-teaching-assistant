"""Shared test fixtures.

Environment variables are set before any `app` import so that settings load
without a real `.env` file. MongoDB is replaced with `mongomock` behind a
small async adapter, and Gemini/Pinecone/LLM calls use in-memory fakes, so
tests never touch the network or spend API credits.
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

import httpx
import mongomock
import pytest

TEST_ENV = {
    "ENVIRONMENT": "test",
    "MONGODB_URI": "mongodb://localhost:27017",
    "LLM_PROVIDER": "gemini",
    "LLM_FALLBACK_PROVIDER": "none",
    "GOOGLE_API_KEY": "test-google-key",
}
os.environ.update(TEST_ENV)

from app.config import Settings, get_settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.container import Services  # noqa: E402
from app.services.llm import LLMProviderError  # noqa: E402
from app.services.vectorstore import ChunkRecord, RetrievedChunk  # noqa: E402
from app.services.web_search import WebResult  # noqa: E402

EMBED_DIM = 8


# ------------------------------------------------------------- mongo adapter
class AsyncCursor:
    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def sort(self, *args: Any, **kwargs: Any) -> AsyncCursor:
        self._cursor = self._cursor.sort(*args, **kwargs)
        return self

    def limit(self, n: int) -> AsyncCursor:
        self._cursor = self._cursor.limit(n)
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        docs = list(self._cursor)
        return docs if length is None else docs[:length]

    def __aiter__(self) -> AsyncCursor:
        self._iter = iter(self._cursor)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class AsyncCollection:
    """Async facade over a mongomock collection (mirrors PyMongo's async API)."""

    def __init__(self, col: Any, index_calls: list[tuple[str, Any, dict[str, Any]]]) -> None:
        self._col = col
        self._index_calls = index_calls

    def find(self, *args: Any, **kwargs: Any) -> AsyncCursor:
        return AsyncCursor(self._col.find(*args, **kwargs))

    async def create_index(self, keys: Any, **options: Any) -> str:
        self._index_calls.append((self._col.name, keys, options))
        return self._col.create_index(keys, **options)

    def __getattr__(self, name: str) -> Any:
        method = getattr(self._col, name)

        async def call(*args: Any, **kwargs: Any) -> Any:
            return method(*args, **kwargs)

        return call


class AsyncDatabase:
    def __init__(self) -> None:
        self._db = mongomock.MongoClient(tz_aware=True)["test_db"]
        self.index_calls: list[tuple[str, Any, dict[str, Any]]] = []

    def __getitem__(self, name: str) -> AsyncCollection:
        return AsyncCollection(self._db[name], self.index_calls)

    async def command(self, name: str) -> dict[str, Any]:
        return {"ok": 1}


class FakeMongo:
    """Drop-in replacement for MongoManager in tests."""

    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy
        self.db = AsyncDatabase()

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def ping(self) -> bool:
        return self.healthy


# --------------------------------------------------------- external services
class FakeEmbedder:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False
        self.queries: list[str] = []

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("embedding API down")
        return [[float(len(t) % 7)] * EMBED_DIM for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [1.0] * EMBED_DIM


class FakeVectorStore:
    """namespace (document_id) -> {vector_id: (chunk, vector)}"""

    def __init__(self) -> None:
        self.namespaces: dict[str, dict[str, tuple[ChunkRecord, list[float]]]] = {}
        self.deleted: list[str] = []
        self.queried: list[str] = []
        self.fail_delete = False

    async def upsert_chunks(
        self, document_id: str, chunks: Sequence[ChunkRecord], vectors: Sequence[list[float]]
    ) -> None:
        ns = self.namespaces.setdefault(document_id, {})
        for chunk, vector in zip(chunks, vectors, strict=True):
            ns[f"{document_id}#{chunk.chunk_index}"] = (chunk, vector)

    async def query(
        self, document_id: str, vector: list[float], top_k: int
    ) -> list[RetrievedChunk]:
        self.queried.append(document_id)
        records = list(self.namespaces.get(document_id, {}).values())[:top_k]
        return [RetrievedChunk(c.chunk_index, c.page_number, c.text, 0.9) for c, _ in records]

    async def fetch_chunks(
        self, document_id: str, chunk_indices: Sequence[int]
    ) -> list[RetrievedChunk]:
        ns = self.namespaces.get(document_id, {})
        out = [ns[f"{document_id}#{i}"][0] for i in chunk_indices if f"{document_id}#{i}" in ns]
        return [RetrievedChunk(c.chunk_index, c.page_number, c.text, 1.0) for c in out]

    async def delete_document(self, document_id: str) -> None:
        if self.fail_delete:
            raise RuntimeError("pinecone down")
        self.namespaces.pop(document_id, None)
        self.deleted.append(document_id)

    async def aclose(self) -> None: ...


class FakeLLM:
    """Scriptable LLM fake.

    - `responder(messages) -> str` customizes text replies (default: `text`).
    - `structured_responder(messages, schema)` customizes structured replies;
      by default every excerpt is graded relevant.
    - `fail` = True or an exception instance makes every call raise.
    """

    def __init__(self, text: str = "This document covers testing.") -> None:
        self.text = text
        self.fail: bool | Exception = False
        self.calls: list[Any] = []
        self.structured_calls: list[Any] = []
        self.responder: Any = None
        self.structured_responder: Any = None

    def _maybe_fail(self) -> None:
        if isinstance(self.fail, Exception):
            raise self.fail
        if self.fail:
            raise LLMProviderError("down")

    async def generate_text(self, messages: Sequence[Any]) -> str:
        self.calls.append(messages)
        self._maybe_fail()
        return self.responder(messages) if self.responder else self.text

    async def generate_structured(self, messages: Sequence[Any], schema: type[Any]) -> Any:
        self.structured_calls.append(messages)
        self._maybe_fail()
        if self.structured_responder:
            return self.structured_responder(messages, schema)
        if "relevant_ids" in schema.model_fields:
            content = messages[-1].content
            # Document excerpts use <excerpt id=..>, web results use [W1].. lines.
            count = content.count("<excerpt id=") + len(re.findall(r"^\[W\d+\]", content, re.M))
            return schema(relevant_ids=list(range(1, count + 1)))
        return schema()


class FakeSearchProvider:
    """Stands in for DuckDuckGo. Set `results` or `fail`; `queries` records calls."""

    def __init__(self) -> None:
        self.results: list[WebResult] = [
            WebResult(
                "FastAPI dependencies",
                "https://fastapi.tiangolo.com/tutorial/dependencies/",
                "FastAPI has a powerful dependency injection system built around Depends.",
            ),
            WebResult(
                "DI on Wikipedia",
                "https://en.wikipedia.org/wiki/Dependency_injection",
                "Dependency injection is a programming technique in which an object receives other objects.",
            ),
        ]
        self.fail = False
        self.queries: list[str] = []

    async def search(self, query: str, max_results: int) -> list[WebResult]:
        self.queries.append(query)
        if self.fail:
            raise TimeoutError("ddg timeout")
        return self.results[:max_results]


# ------------------------------------------------------------------ fixtures
@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that tries to open a real network connection (no API credits spent)."""
    import socket

    def guard(self: socket.socket, address: Any) -> None:
        raise RuntimeError(f"Network access is blocked in tests (tried to connect to {address!r})")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, embedding_batch_delay_seconds=0)  # type: ignore[call-arg]


@pytest.fixture
def fake_mongo() -> FakeMongo:
    return FakeMongo()


@pytest.fixture
def services() -> Services:
    return Services(
        llm=FakeLLM(),
        embedder=FakeEmbedder(),
        vector_store=FakeVectorStore(),
        web_provider=FakeSearchProvider(),
    )


@pytest.fixture
async def client(
    settings: Settings, fake_mongo: FakeMongo, services: Services
) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client bound to an app whose lifespan runs against the fakes."""
    app = create_app(settings, mongo=fake_mongo, services=services)  # type: ignore[arg-type]
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


# ------------------------------------------------------------- test files
def make_pdf(pages: list[str]) -> bytes:
    """Build a real PDF in memory; an empty string makes a blank (scanned-like) page."""
    import pymupdf

    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text, fontsize=10)
    data = doc.tobytes()
    doc.close()
    return bytes(data)


def make_docx(paragraphs: list[str]) -> bytes:
    """Build a minimal valid DOCX (zip with word/document.xml) in memory."""
    import io
    import zipfile
    from xml.sax.saxutils import escape

    body = "".join(f"<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>" for p in paragraphs)
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("word/document.xml", document_xml)
    return buf.getvalue()
