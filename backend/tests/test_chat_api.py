"""API tests for /chat: sessions, messages, ownership, and error handling."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from app.prompts.qa import ANSWER_SYSTEM_PROMPT, REWRITE_SYSTEM_PROMPT
from app.services.chat import make_title
from app.services.container import Services
from app.services.llm import LLMRateLimitError

from tests.conftest import FakeMongo, make_pdf

PDF = make_pdf(["Dependency injection passes dependencies in. " * 20] * 2)


@pytest.fixture
async def ready_doc(client: httpx.AsyncClient) -> str:
    response = await client.post("/documents", files={"file": ("di.pdf", PDF, "application/pdf")})
    doc_id = response.json()["id"]
    assert (await client.get(f"/documents/{doc_id}")).json()["status"] == "ready"
    return doc_id


@pytest.fixture
def answering(services: Services) -> Any:
    """Make the fake LLM answer with a page citation."""

    def responder(messages: Any) -> str:
        system = messages[0].content
        if system == REWRITE_SYSTEM_PROMPT:
            return "Why is dependency injection useful?"
        if system == ANSWER_SYSTEM_PROMPT:
            return "It passes dependencies in (Page 2)."
        return "summary"

    services.llm.responder = responder  # type: ignore[attr-defined]
    return services.llm


async def new_session(client: httpx.AsyncClient, doc_id: str) -> str:
    response = await client.post("/chat/sessions", json={"document_id": doc_id})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def ask(client: httpx.AsyncClient, session_id: str, content: str) -> httpx.Response:
    return await client.post(
        f"/chat/sessions/{session_id}/messages", json={"content": content, "web_search": False}
    )


async def insert_other_users_session(mongo: FakeMongo, doc_id: str) -> str:
    now = datetime.now(UTC)
    result = await mongo.db["chat_sessions"].insert_one(
        {
            "user_id": "someone-else",
            "document_id": doc_id,
            "title": "x",
            "created_at": now,
            "updated_at": now,
        }
    )
    return str(result.inserted_id)


# ------------------------------------------------------------------ sessions
async def test_create_session(client: httpx.AsyncClient, ready_doc: str) -> None:
    response = await client.post("/chat/sessions", json={"document_id": ready_doc})
    assert response.status_code == 201
    assert response.json()["title"] == "New chat"
    assert response.json()["document_id"] == ready_doc


async def test_cannot_create_session_for_processing_document(
    client: httpx.AsyncClient, fake_mongo: FakeMongo
) -> None:
    result = await fake_mongo.db["documents"].insert_one(
        {
            "owner_id": "local-dev-user",
            "filename": "x.pdf",
            "file_type": "pdf",
            "status": "processing",
            "created_at": datetime.now(UTC),
        }
    )
    response = await client.post("/chat/sessions", json={"document_id": str(result.inserted_id)})
    assert response.status_code == 409


async def test_cannot_create_session_for_other_users_document(
    client: httpx.AsyncClient, fake_mongo: FakeMongo
) -> None:
    result = await fake_mongo.db["documents"].insert_one(
        {
            "owner_id": "someone-else",
            "filename": "x.pdf",
            "file_type": "pdf",
            "status": "ready",
            "created_at": datetime.now(UTC),
        }
    )
    response = await client.post("/chat/sessions", json={"document_id": str(result.inserted_id)})
    assert response.status_code == 404


async def test_list_sessions_filters_by_document_and_owner(
    client: httpx.AsyncClient, ready_doc: str, fake_mongo: FakeMongo
) -> None:
    mine = await new_session(client, ready_doc)
    await insert_other_users_session(fake_mongo, ready_doc)

    sessions = (await client.get("/chat/sessions", params={"document_id": ready_doc})).json()[
        "sessions"
    ]
    assert [s["id"] for s in sessions] == [mine]
    assert (await client.get("/chat/sessions", params={"document_id": "other"})).json()[
        "sessions"
    ] == []


# ------------------------------------------------------------------ messages
async def test_send_message_returns_cited_answer(
    client: httpx.AsyncClient, ready_doc: str, answering: Any
) -> None:
    session_id = await new_session(client, ready_doc)

    response = await ask(client, session_id, "What is dependency injection?")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user_message"]["role"] == "user"
    assert body["assistant_message"]["content"] == "It passes dependencies in (Page 2)."
    assert body["assistant_message"]["sources"] == [{"type": "document", "page": 2}]
    assert body["assistant_message"]["web_search_enabled"] is False


async def test_messages_are_persisted_in_order_and_title_set(
    client: httpx.AsyncClient, ready_doc: str, answering: Any
) -> None:
    session_id = await new_session(client, ready_doc)
    await ask(client, session_id, "What is dependency injection?")
    await ask(client, session_id, "Why is it useful?")

    messages = (await client.get(f"/chat/sessions/{session_id}/messages")).json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[2]["content"] == "Why is it useful?"

    session = (await client.get("/chat/sessions")).json()["sessions"][0]
    assert session["title"] == "What is dependency injection?"  # from the FIRST question
    assert session["updated_at"] > session["created_at"]


async def test_follow_up_uses_history(
    client: httpx.AsyncClient, ready_doc: str, answering: Any, services: Services
) -> None:
    session_id = await new_session(client, ready_doc)
    await ask(client, session_id, "What is dependency injection?")
    await ask(client, session_id, "Why is it useful?")

    assert services.embedder.queries[-1] == "Why is dependency injection useful?"  # type: ignore[attr-defined]


async def test_blank_message_rejected(client: httpx.AsyncClient, ready_doc: str) -> None:
    session_id = await new_session(client, ready_doc)
    assert (await ask(client, session_id, "   ")).status_code == 422


async def test_rate_limit_returns_503_and_saves_nothing(
    client: httpx.AsyncClient, ready_doc: str, services: Services, answering: Any
) -> None:
    session_id = await new_session(client, ready_doc)
    services.llm.fail = LLMRateLimitError("rate-limited, try again")  # type: ignore[attr-defined]
    # Make sure the failing call is reached (grading uses the LLM).
    response = await ask(client, session_id, "What is dependency injection?")

    assert response.status_code == 503
    assert "rate-limited" in response.json()["error"]["message"]
    assert (await client.get(f"/chat/sessions/{session_id}/messages")).json()["messages"] == []


# ----------------------------------------------------------------- ownership
@pytest.mark.parametrize("method", ["list", "send", "delete"])
async def test_other_users_session_is_404(
    client: httpx.AsyncClient, ready_doc: str, fake_mongo: FakeMongo, method: str
) -> None:
    other = await insert_other_users_session(fake_mongo, ready_doc)
    if method == "list":
        response = await client.get(f"/chat/sessions/{other}/messages")
    elif method == "send":
        response = await ask(client, other, "hi")
    else:
        response = await client.delete(f"/chat/sessions/{other}")
    assert response.status_code == 404
    assert await fake_mongo.db["chat_sessions"].count_documents({"user_id": "someone-else"}) == 1


async def test_send_after_document_deleted_is_404(
    client: httpx.AsyncClient, ready_doc: str, answering: Any
) -> None:
    session_id = await new_session(client, ready_doc)
    assert (await client.delete(f"/documents/{ready_doc}")).status_code == 204
    # The session was cascade-deleted with the document.
    assert (await ask(client, session_id, "hi")).status_code == 404


async def test_delete_session_removes_messages(
    client: httpx.AsyncClient, ready_doc: str, answering: Any, fake_mongo: FakeMongo
) -> None:
    session_id = await new_session(client, ready_doc)
    await ask(client, session_id, "What is dependency injection?")

    assert (await client.delete(f"/chat/sessions/{session_id}")).status_code == 204
    assert await fake_mongo.db["chat_messages"].count_documents({}) == 0
    assert (await client.get(f"/chat/sessions/{session_id}/messages")).status_code == 404


# -------------------------------------------------------------------- titles
@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("  What is   DI? ", "What is DI?"),
        ("word " * 30, ("word " * 12).strip() + "…"),
    ],
)
def test_make_title(question: str, expected: str) -> None:
    assert make_title(question) == expected


# --------------------------------------------------------------- web search
@pytest.fixture
def web_answering(services: Services) -> Any:
    from app.prompts.web import ANSWER_WITH_WEB_SYSTEM_PROMPT, SEARCH_QUERY_SYSTEM_PROMPT

    def responder(messages: Any) -> str:
        system = messages[0].content
        if system == SEARCH_QUERY_SYSTEM_PROMPT:
            return "fastapi dependency injection"
        if system == ANSWER_WITH_WEB_SYSTEM_PROMPT:
            return "**From your document**\nX (Page 1).\n\n**Additional context from the web**\nY [W2]."
        if system == ANSWER_SYSTEM_PROMPT:
            return "X (Page 1)."
        return "summary"

    services.llm.responder = responder  # type: ignore[attr-defined]
    return services.llm


async def test_usage_endpoint(client: httpx.AsyncClient, settings: Any) -> None:
    body = (await client.get("/usage/web-search")).json()
    assert body["limit"] == settings.web_search_daily_limit
    assert body["used"] == 0
    assert body["remaining"] == settings.web_search_daily_limit
    assert len(body["date"]) == 10


async def test_web_search_message_stores_typed_web_sources(
    client: httpx.AsyncClient, ready_doc: str, web_answering: Any
) -> None:
    session_id = await new_session(client, ready_doc)
    response = await client.post(
        f"/chat/sessions/{session_id}/messages",
        json={"content": "How does FastAPI do DI?", "web_search": True},
    )
    assert response.status_code == 200, response.text
    assistant = response.json()["assistant_message"]
    assert assistant["web_search_enabled"] is True
    assert assistant["sources"] == [
        {"type": "document", "page": 1},
        {
            "type": "web",
            "title": "DI on Wikipedia",
            "url": "https://en.wikipedia.org/wiki/Dependency_injection",
        },
    ]
    stored = (await client.get(f"/chat/sessions/{session_id}/messages")).json()["messages"]
    assert stored[1]["sources"] == assistant["sources"]
    assert (await client.get("/usage/web-search")).json()["used"] == 1


async def test_web_search_limit_reached_answers_from_document(
    client: httpx.AsyncClient, ready_doc: str, web_answering: Any, settings: Any, services: Services
) -> None:
    from app.prompts.web import WEB_LIMIT_REACHED_NOTICE

    settings.web_search_daily_limit = 1
    settings.web_search_cache_enabled = False
    session_id = await new_session(client, ready_doc)
    for content in ("first question", "second question"):
        response = await client.post(
            f"/chat/sessions/{session_id}/messages", json={"content": content, "web_search": True}
        )
    assistant = response.json()["assistant_message"]
    assert WEB_LIMIT_REACHED_NOTICE in assistant["content"]
    assert all(s["type"] == "document" for s in assistant["sources"])
    assert len(services.web_provider.queries) == 1  # type: ignore[attr-defined]
    usage = (await client.get("/usage/web-search")).json()
    assert (usage["used"], usage["remaining"]) == (1, 0)
