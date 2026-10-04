"""Tests for IngestionService.run failure handling (with fake services)."""

from __future__ import annotations

from bson import ObjectId

from app.config import Settings
from app.db.repositories.documents import DocumentRepository
from app.schemas.document import FileType
from app.services.ingestion import IngestionService
from tests.conftest import FakeEmbedder, FakeLLM, FakeMongo, FakeVectorStore, make_pdf

PDF = make_pdf(["Some real course content about recursion. " * 30] * 2)


async def setup(settings: Settings) -> tuple[IngestionService, FakeMongo, str, dict[str, object]]:
    mongo = FakeMongo()
    repo = DocumentRepository(mongo.db)
    doc_id = await repo.create("u1", "x.pdf", FileType.PDF)
    fakes: dict[str, object] = {
        "embedder": FakeEmbedder(),
        "store": FakeVectorStore(),
        "llm": FakeLLM(),
    }
    service = IngestionService(settings, repo, fakes["embedder"], fakes["store"], fakes["llm"])  # type: ignore[arg-type]
    return service, mongo, doc_id, fakes


async def get_doc(mongo: FakeMongo, doc_id: str) -> dict[str, object]:
    return await mongo.db["documents"].find_one({"_id": ObjectId(doc_id)})


async def test_summary_failure_does_not_fail_ingestion(settings: Settings) -> None:
    service, mongo, doc_id, fakes = await setup(settings)
    fakes["llm"].fail = True  # type: ignore[attr-defined]
    await service.run(doc_id, "x.pdf", FileType.PDF, PDF)
    doc = await get_doc(mongo, doc_id)
    assert doc["status"] == "ready"
    assert doc["summary"] is None


async def test_embedding_failure_marks_failed_and_cleans_up(settings: Settings) -> None:
    service, mongo, doc_id, fakes = await setup(settings)
    fakes["embedder"].fail = True  # type: ignore[attr-defined]
    await service.run(doc_id, "x.pdf", FileType.PDF, PDF)
    doc = await get_doc(mongo, doc_id)
    assert doc["status"] == "failed"
    # Generic message: internal error details are not exposed to the user.
    assert "embedding API down" not in doc["error_message"]
    assert doc_id in fakes["store"].deleted  # type: ignore[attr-defined]


async def test_large_document_is_upserted_in_batches(settings: Settings) -> None:
    settings.chunk_size = 200
    settings.chunk_overlap = 20
    service, mongo, doc_id, fakes = await setup(settings)
    big = make_pdf(["Lots of text here. " * 120] * 10)
    await service.run(doc_id, "x.pdf", FileType.PDF, big)
    doc = await get_doc(mongo, doc_id)
    assert doc["status"] == "ready"
    assert doc["chunk_count"] > 100  # forces more than one pipeline batch
    assert fakes["embedder"].calls >= 2  # type: ignore[attr-defined]
    assert len(fakes["store"].namespaces[doc_id]) == doc["chunk_count"]  # type: ignore[attr-defined]


async def test_summary_prompt_treats_document_as_data(settings: Settings) -> None:
    service, _, doc_id, fakes = await setup(settings)
    await service.run(doc_id, "x.pdf", FileType.PDF, PDF)
    system, human = fakes["llm"].calls[0]  # type: ignore[attr-defined]
    assert "not instructions" in system.content
    assert "<document_excerpts>" in human.content
