"""Smoke tests for the Streamlit pages using AppTest and a mocked api_client."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

import api_client

DOCS = [
    {"id": "d1", "filename": "notes.pdf", "file_type": "pdf", "status": "ready", "page_count": 3,
     "chunk_count": 9, "summary": "Notes about dependency injection.", "error_message": None,
     "created_at": "2026-10-04T09:00:00Z"},
    {"id": "d2", "filename": "scan.pdf", "file_type": "pdf", "status": "failed", "page_count": None,
     "chunk_count": None, "summary": None, "error_message": "Scanned PDFs/OCR are not supported.",
     "created_at": "2026-10-04T08:00:00Z"},
]
MESSAGES = [
    {"id": "m1", "session_id": "s1", "role": "user", "content": "What is DI?", "web_search_enabled": True,
     "sources": [], "created_at": "2026-10-04T09:01:00Z"},
    {"id": "m2", "session_id": "s1", "role": "assistant", "content": "DI passes dependencies in (Page 3).",
     "web_search_enabled": True, "created_at": "2026-10-04T09:01:05Z",
     "sources": [{"type": "document", "page": 3},
                 {"type": "web", "title": "FastAPI docs", "url": "https://fastapi.tiangolo.com"}]},
]
QUIZ = {
    "id": "q1", "document_id": "d1", "topic": None, "difficulty": "easy", "question_type": "mcq",
    "question_count": 1, "created_at": "2026-10-04T09:00:00Z", "attempts": [],
    "questions": [{"id": 0, "question": "What is DI?", "options": ["A", "B", "C", "D"], "source_page": 1}],
}
RESULT = {
    "id": "a1", "quiz_id": "q1", "score": 1.0, "total": 1, "percentage": 100.0, "created_at": "x",
    "results": [{"question_id": 0, "question": "What is DI?", "user_answer": "B", "correct_answer": "B",
                 "is_correct": True, "score": 1.0, "feedback": "Correct!", "explanation": "Page 1 says so.",
                 "source_page": 1}],
}


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Replace every api_client endpoint with an in-memory stub; record calls."""
    calls: dict[str, list[Any]] = {}

    def stub(name: str, result: Any) -> None:
        def fn(*args: Any, **kwargs: Any) -> Any:
            calls.setdefault(name, []).append((args, kwargs))
            return result(*args) if callable(result) else result
        monkeypatch.setattr(api_client, name, fn)

    stub("health", {"status": "ok", "mongo": "ok", "version": "0.1.0"})
    stub("list_documents", DOCS)
    stub("list_sessions", [{"id": "s1", "document_id": "d1", "title": "What is DI?",
                            "created_at": "x", "updated_at": "x"}])
    stub("list_messages", MESSAGES)
    stub("web_search_usage", {"date": "2026-10-04", "limit": 20, "used": 3, "remaining": 17})
    stub("create_session", {"id": "s2"})
    stub("send_message", {"user_message": MESSAGES[0], "assistant_message": MESSAGES[1]})
    stub("list_quizzes", [])
    stub("submit_quiz", RESULT)
    return calls


FRONTEND = Path(__file__).resolve().parent.parent


def run(path: str, **state: Any) -> AppTest:
    at = AppTest.from_file(str(FRONTEND / path), default_timeout=10)
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def all_text(at: AppTest) -> str:
    parts = [e.value for e in at.markdown] + [e.value for e in at.caption]
    parts += [e.value for e in at.error] + [e.value for e in at.info] + [e.value for e in at.success]
    parts += [e.proto.body for e in at.get("html")]
    return "\n".join(str(p) for p in parts)


def test_home_shows_server_status(api: dict[str, list[Any]]) -> None:
    at = run("app.py")
    assert not at.exception
    assert "Server is up" in all_text(at)


def test_home_shows_friendly_error_when_server_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def down() -> Any:
        raise api_client.APIError("Cannot reach the server. Please try again in a minute.")
    monkeypatch.setattr(api_client, "health", down)
    at = run("app.py")
    assert not at.exception
    assert "Cannot reach the server" in [w.value for w in at.warning][0]


def test_documents_page_lists_status_summary_and_errors(api: dict[str, list[Any]]) -> None:
    at = run("pages/documents.py")
    assert not at.exception
    text = all_text(at)
    assert "notes.pdf" in text and "3 pages" in text
    assert "Notes about dependency injection." in text
    assert "OCR are not supported" in text


def test_chat_page_renders_history_and_labeled_sources(api: dict[str, list[Any]]) -> None:
    at = run("pages/chat.py", chat_session_id="s1", chat_doc_id="d1")
    assert not at.exception
    text = all_text(at)
    assert "DI passes dependencies in (Page 3)." in text
    assert "Page 3" in text
    assert 'href="https://fastapi.tiangolo.com"' in text  # clickable web link
    assert "not course material" in text  # web clearly distinguished
    assert "17 of 20 web searches left today" in text
    assert at.toggle(key="web_search_toggle").label == "Search the web"


def test_chat_input_sends_with_web_toggle(api: dict[str, list[Any]]) -> None:
    at = run("pages/chat.py", chat_doc_id="d1")
    at.toggle(key="web_search_toggle").set_value(True).run()
    at.chat_input[0].set_value("What is DI?").run()
    assert not at.exception
    (args, _), = api["send_message"]
    assert args == ("s2", "What is DI?", True)  # new session created, toggle respected
    assert api["create_session"][0][0] == ("d1",)


def test_quiz_take_and_submit(api: dict[str, list[Any]]) -> None:
    at = run("pages/quiz.py", active_quiz=QUIZ, selected_document_id="d1")
    assert not at.exception
    at.radio[0].set_value("B")
    at.button(key="FormSubmitter:quiz-q1-Submit answers").click().run()
    assert not at.exception
    (args, _), = api["submit_quiz"]
    assert args == ("q1", [{"question_id": 0, "answer": "B"}])
    text = all_text(at)
    assert "Page 1 says so." in text and "Source: Page 1" in text


def test_pages_handle_no_documents(api: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_client, "list_documents", lambda: [])
    for page in ("pages/chat.py", "pages/quiz.py"):
        at = run(page)
        assert not at.exception
        assert "No ready documents yet" in all_text(at)
