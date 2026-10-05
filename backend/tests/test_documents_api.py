"""API tests for /documents: upload validation, background ingestion, ownership, delete."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from app.services.container import Services
from bson import ObjectId

from tests.conftest import FakeMongo, FakeVectorStore, make_docx, make_pdf

PDF_PAGES = [f"Page {i}: dependency injection explained. " * 20 for i in range(1, 4)]


async def upload(client: httpx.AsyncClient, name: str, data: bytes) -> httpx.Response:
    return await client.post("/documents", files={"file": (name, data, "application/octet-stream")})


async def insert_doc(mongo: FakeMongo, owner_id: str, status: str = "ready") -> str:
    result = await mongo.db["documents"].insert_one(
        {
            "owner_id": owner_id,
            "filename": "other.pdf",
            "file_type": "pdf",
            "status": status,
            "created_at": datetime.now(UTC),
        }
    )
    return str(result.inserted_id)


# -------------------------------------------------------------------- upload
async def test_upload_pdf_is_ingested(client: httpx.AsyncClient, services: Services) -> None:
    response = await upload(client, "notes.pdf", make_pdf(PDF_PAGES))
    assert response.status_code == 202
    doc_id = response.json()["id"]
    assert response.json()["status"] == "processing"

    # httpx's ASGI transport waits for background tasks, so ingestion is done.
    doc = (await client.get(f"/documents/{doc_id}")).json()
    assert doc["status"] == "ready", doc
    assert doc["page_count"] == 3
    assert doc["chunk_count"] > 0
    assert doc["summary"] == "This document covers testing."

    store: FakeVectorStore = services.vector_store  # type: ignore[assignment]
    stored = store.namespaces[doc_id]
    assert len(stored) == doc["chunk_count"]
    chunk, _ = next(iter(stored.values()))
    assert chunk.page_number in {1, 2, 3}


async def test_upload_docx_is_ingested(client: httpx.AsyncClient) -> None:
    response = await upload(client, "notes.docx", make_docx(["Hello world. " * 50] * 10))
    assert response.status_code == 202
    doc = (await client.get(f"/documents/{response.json()['id']}")).json()
    assert doc["status"] == "ready"
    assert doc["file_type"] == "docx"


async def test_scanned_pdf_fails_with_clear_message(client: httpx.AsyncClient) -> None:
    response = await upload(client, "scan.pdf", make_pdf(["", ""]))
    doc = (await client.get(f"/documents/{response.json()['id']}")).json()
    assert doc["status"] == "failed"
    assert "OCR" in doc["error_message"]


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("notes.txt", b"plain text"),
        ("fake.pdf", b"this is not a pdf"),
        ("fake.docx", b"PK\x03\x04not a real zip"),
        ("empty.pdf", b""),
    ],
)
async def test_invalid_files_rejected(client: httpx.AsyncClient, name: str, data: bytes) -> None:
    response = await upload(client, name, data)
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"


async def test_file_too_large_rejected(client: httpx.AsyncClient, settings: Any) -> None:
    settings.max_upload_size_mb = 1
    response = await upload(client, "big.pdf", b"%PDF-" + b"0" * (1024 * 1024 + 1))
    assert response.status_code == 413


async def test_per_user_quota(client: httpx.AsyncClient, settings: Any) -> None:
    settings.max_documents_per_user = 1
    assert (await upload(client, "a.pdf", make_pdf(PDF_PAGES))).status_code == 202
    response = await upload(client, "b.pdf", make_pdf(PDF_PAGES))
    assert response.status_code == 409


async def test_global_quota(
    client: httpx.AsyncClient, fake_mongo: FakeMongo, settings: Any
) -> None:
    settings.max_documents_total = 1
    await insert_doc(fake_mongo, owner_id="someone-else")
    response = await upload(client, "a.pdf", make_pdf(PDF_PAGES))
    assert response.status_code == 409
    assert "capacity" in response.json()["error"]["message"]


# ----------------------------------------------------------------- ownership
async def test_list_only_returns_own_documents(
    client: httpx.AsyncClient, fake_mongo: FakeMongo
) -> None:
    await insert_doc(fake_mongo, owner_id="someone-else")
    await upload(client, "mine.pdf", make_pdf(PDF_PAGES))
    docs = (await client.get("/documents")).json()["documents"]
    assert [d["filename"] for d in docs] == ["mine.pdf"]


async def test_cannot_read_other_users_document(
    client: httpx.AsyncClient, fake_mongo: FakeMongo
) -> None:
    other_id = await insert_doc(fake_mongo, owner_id="someone-else")
    response = await client.get(f"/documents/{other_id}")
    assert response.status_code == 404


async def test_cannot_delete_other_users_document(
    client: httpx.AsyncClient, fake_mongo: FakeMongo, services: Services
) -> None:
    other_id = await insert_doc(fake_mongo, owner_id="someone-else")
    response = await client.delete(f"/documents/{other_id}")
    assert response.status_code == 404
    assert await fake_mongo.db["documents"].count_documents({}) == 1
    assert other_id not in services.vector_store.deleted  # type: ignore[attr-defined]


@pytest.mark.parametrize("bad_id", ["not-an-id", str(ObjectId())])
async def test_unknown_or_invalid_id_is_404(client: httpx.AsyncClient, bad_id: str) -> None:
    assert (await client.get(f"/documents/{bad_id}")).status_code == 404


# -------------------------------------------------------------------- delete
async def test_delete_cascades(
    client: httpx.AsyncClient, fake_mongo: FakeMongo, services: Services
) -> None:
    doc_id = (await upload(client, "notes.pdf", make_pdf(PDF_PAGES))).json()["id"]
    db = fake_mongo.db
    session = await db["chat_sessions"].insert_one(
        {"user_id": "local-dev-user", "document_id": doc_id}
    )
    await db["chat_messages"].insert_one({"session_id": str(session.inserted_id), "content": "hi"})
    quiz = await db["quizzes"].insert_one({"user_id": "local-dev-user", "document_id": doc_id})
    await db["quiz_attempts"].insert_one({"quiz_id": str(quiz.inserted_id), "score": 1})
    # Data of another document must survive.
    await db["chat_sessions"].insert_one({"user_id": "local-dev-user", "document_id": "other"})

    response = await client.delete(f"/documents/{doc_id}")

    assert response.status_code == 204
    assert (await client.get(f"/documents/{doc_id}")).status_code == 404
    assert doc_id not in services.vector_store.namespaces  # type: ignore[attr-defined]
    assert await db["chat_messages"].count_documents({}) == 0
    assert await db["quizzes"].count_documents({}) == 0
    assert await db["quiz_attempts"].count_documents({}) == 0
    assert await db["chat_sessions"].count_documents({}) == 1


async def test_delete_while_processing_is_conflict(
    client: httpx.AsyncClient, fake_mongo: FakeMongo
) -> None:
    doc_id = await insert_doc(fake_mongo, owner_id="local-dev-user", status="processing")
    assert (await client.delete(f"/documents/{doc_id}")).status_code == 409


async def test_delete_keeps_record_when_pinecone_fails(
    client: httpx.AsyncClient, fake_mongo: FakeMongo, services: Services
) -> None:
    doc_id = await insert_doc(fake_mongo, owner_id="local-dev-user")
    services.vector_store.fail_delete = True  # type: ignore[attr-defined]
    response = await client.delete(f"/documents/{doc_id}")
    assert response.status_code == 503
    assert (await client.get(f"/documents/{doc_id}")).status_code == 200


# --------------------------------------------------------- restart recovery
async def test_processing_documents_failed_on_startup(settings: Any, services: Services) -> None:
    from app.main import create_app

    mongo = FakeMongo()
    stuck_id = await insert_doc(mongo, owner_id="local-dev-user", status="processing")
    app = create_app(settings, mongo=mongo, services=services)  # type: ignore[arg-type]
    async with app.router.lifespan_context(app):
        doc = await mongo.db["documents"].find_one({"_id": ObjectId(stuck_id)})
    assert doc["status"] == "failed"
    assert "interrupted" in doc["error_message"]
