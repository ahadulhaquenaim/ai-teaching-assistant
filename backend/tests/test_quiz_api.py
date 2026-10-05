"""API tests for /quizzes: generation, hidden answers, scoring, ownership."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from app.schemas.quiz import (
    GeneratedQuestion,
    GeneratedQuiz,
    ShortAnswerGrade,
    ShortAnswerGrades,
    SupportCheck,
)
from app.services.container import Services
from app.services.llm import LLMRateLimitError

from tests.conftest import FakeMongo, make_pdf

PDF = make_pdf(["Dependency injection passes dependencies in. " * 20] * 3)
OPTS = ["Passing dependencies in", "Global variables", "Inheritance", "Monkey patching"]


def questions(n: int, short: bool = False) -> list[GeneratedQuestion]:
    return [
        GeneratedQuestion(
            question=f"Question number {i} about DI?",
            options=[] if short else OPTS,
            correct_answer="Passing dependencies in from outside." if short else OPTS[0],
            explanation=f"Explanation {i}.",
            source_page=1 + i % 3,
        )
        for i in range(n)
    ]


@pytest.fixture
def quiz_llm(services: Services) -> Any:
    llm = services.llm
    llm.generated = questions(3)  # type: ignore[attr-defined]
    llm.short_grades = {}  # type: ignore[attr-defined]

    def structured(messages: Any, schema: Any) -> Any:
        if schema is GeneratedQuiz:
            return GeneratedQuiz(questions=llm.generated)  # type: ignore[attr-defined]
        if schema is SupportCheck:
            return SupportCheck(supported_ids=list(range(1, 21)))
        if schema is ShortAnswerGrades:
            return ShortAnswerGrades(
                grades=[ShortAnswerGrade(**g) for g in llm.short_grades.values()]
            )  # type: ignore[attr-defined]
        return schema(relevant_ids=[1, 2, 3])

    llm.structured_responder = structured  # type: ignore[attr-defined]
    return llm


@pytest.fixture
async def ready_doc(client: httpx.AsyncClient) -> str:
    response = await client.post("/documents", files={"file": ("di.pdf", PDF, "application/pdf")})
    return response.json()["id"]


async def create(client: httpx.AsyncClient, doc_id: str, **overrides: Any) -> httpx.Response:
    body = {
        "document_id": doc_id,
        "difficulty": "easy",
        "number_of_questions": 3,
        "question_type": "mcq",
    }
    body.update(overrides)
    return await client.post("/quizzes", json=body)


# ------------------------------------------------------------------ create
async def test_create_quiz_hides_answers(
    client: httpx.AsyncClient, ready_doc: str, quiz_llm: Any
) -> None:
    response = await create(client, ready_doc, topic="dependency injection")
    assert response.status_code == 201, response.text
    quiz = response.json()
    assert quiz["question_count"] == 3
    assert quiz["topic"] == "dependency injection"
    first = quiz["questions"][0]
    assert set(first) == {"id", "question", "options", "source_page"}  # no answer/explanation
    assert len(first["options"]) == 4


async def test_too_many_questions(client: httpx.AsyncClient, ready_doc: str, settings: Any) -> None:
    response = await create(client, ready_doc, number_of_questions=settings.quiz_max_questions + 1)
    assert response.status_code == 422


async def test_generation_failure_is_422(
    client: httpx.AsyncClient, ready_doc: str, quiz_llm: Any
) -> None:
    quiz_llm.generated = []
    response = await create(client, ready_doc)
    assert response.status_code == 422
    assert "Could not generate" in response.json()["error"]["message"]


async def test_rate_limited_generation_is_503(
    client: httpx.AsyncClient, ready_doc: str, quiz_llm: Any
) -> None:
    quiz_llm.fail = LLMRateLimitError("rate-limited")
    assert (await create(client, ready_doc)).status_code == 503


async def test_cannot_quiz_other_users_document(
    client: httpx.AsyncClient, fake_mongo: FakeMongo
) -> None:
    result = await fake_mongo.db["documents"].insert_one(
        {
            "owner_id": "someone-else",
            "filename": "x.pdf",
            "file_type": "pdf",
            "status": "ready",
            "chunk_count": 3,
            "created_at": datetime.now(UTC),
        }
    )
    assert (await create(client, str(result.inserted_id))).status_code == 404


# ------------------------------------------------------------------ submit
async def test_submit_mcq_exact_scoring(
    client: httpx.AsyncClient, ready_doc: str, quiz_llm: Any
) -> None:
    quiz = (await create(client, ready_doc)).json()
    answers = [
        {"question_id": 0, "answer": OPTS[0]},  # correct
        {"question_id": 1, "answer": OPTS[2]},  # wrong
    ]  # question 2 unanswered

    response = await client.post(f"/quizzes/{quiz['id']}/submit", json={"answers": answers})

    assert response.status_code == 200, response.text
    attempt = response.json()
    assert (attempt["score"], attempt["total"], attempt["percentage"]) == (1.0, 3, 33.3)
    results = attempt["results"]
    assert [r["is_correct"] for r in results] == [True, False, False]
    assert results[1]["correct_answer"] == OPTS[0]  # revealed after submitting
    assert results[1]["explanation"] == "Explanation 1."
    assert results[1]["source_page"] == 2
    assert "No answer given" in results[2]["feedback"]

    stored = (await client.get(f"/quizzes/{quiz['id']}")).json()
    assert [a["id"] for a in stored["attempts"]] == [attempt["id"]]


async def test_submit_unknown_question_id(
    client: httpx.AsyncClient, ready_doc: str, quiz_llm: Any
) -> None:
    quiz = (await create(client, ready_doc)).json()
    response = await client.post(
        f"/quizzes/{quiz['id']}/submit", json={"answers": [{"question_id": 42, "answer": "x"}]}
    )
    assert response.status_code == 422


async def test_submit_short_answer_llm_scoring(
    client: httpx.AsyncClient, ready_doc: str, quiz_llm: Any
) -> None:
    quiz_llm.generated = questions(2, short=True)
    quiz = (
        await create(client, ready_doc, question_type="short_answer", number_of_questions=2)
    ).json()
    quiz_llm.short_grades = {0: {"question_id": 0, "score": 0.5, "feedback": "Partly right."}}

    response = await client.post(
        f"/quizzes/{quiz['id']}/submit",
        json={
            "answers": [
                {"question_id": 0, "answer": "You pass things in"},
                {"question_id": 1, "answer": "  "},
            ]
        },
    )

    attempt = response.json()
    assert attempt["score"] == 0.5
    assert attempt["results"][0]["feedback"] == "Partly right."
    assert attempt["results"][0]["is_correct"] is False
    assert attempt["results"][1]["feedback"] == "No answer given."  # graded without the LLM
    grading_call = quiz_llm.structured_calls[-1]
    assert "<student_answer>You pass things in</student_answer>" in grading_call[1].content


async def test_short_answer_grading_failure_saves_nothing(
    client: httpx.AsyncClient, ready_doc: str, quiz_llm: Any
) -> None:
    quiz_llm.generated = questions(1, short=True)
    quiz = (
        await create(client, ready_doc, question_type="short_answer", number_of_questions=1)
    ).json()
    quiz_llm.fail = LLMRateLimitError("rate-limited")
    response = await client.post(
        f"/quizzes/{quiz['id']}/submit", json={"answers": [{"question_id": 0, "answer": "x"}]}
    )
    assert response.status_code == 503
    quiz_llm.fail = False
    assert (await client.get(f"/quizzes/{quiz['id']}")).json()["attempts"] == []


# --------------------------------------------------------------- ownership
async def test_other_users_quiz_is_404(client: httpx.AsyncClient, fake_mongo: FakeMongo) -> None:
    result = await fake_mongo.db["quizzes"].insert_one(
        {
            "user_id": "someone-else",
            "document_id": "d",
            "topic": None,
            "difficulty": "easy",
            "question_type": "mcq",
            "questions": [],
            "created_at": datetime.now(UTC),
        }
    )
    quiz_id = str(result.inserted_id)
    assert (await client.get(f"/quizzes/{quiz_id}")).status_code == 404
    assert (
        await client.post(f"/quizzes/{quiz_id}/submit", json={"answers": []})
    ).status_code == 404
    assert (await client.get("/quizzes")).json()["quizzes"] == []


async def test_list_filters_by_document(
    client: httpx.AsyncClient, ready_doc: str, quiz_llm: Any
) -> None:
    quiz = (await create(client, ready_doc)).json()
    assert [
        q["id"]
        for q in (await client.get("/quizzes", params={"document_id": ready_doc})).json()["quizzes"]
    ] == [quiz["id"]]
    assert (await client.get("/quizzes", params={"document_id": "other"})).json()["quizzes"] == []


async def test_deleting_document_deletes_quizzes_and_attempts(
    client: httpx.AsyncClient, ready_doc: str, quiz_llm: Any, fake_mongo: FakeMongo
) -> None:
    quiz = (await create(client, ready_doc)).json()
    await client.post(
        f"/quizzes/{quiz['id']}/submit", json={"answers": [{"question_id": 0, "answer": OPTS[0]}]}
    )
    assert (await client.delete(f"/documents/{ready_doc}")).status_code == 204
    assert await fake_mongo.db["quizzes"].count_documents({}) == 0
    assert await fake_mongo.db["quiz_attempts"].count_documents({}) == 0


async def test_mcq_options_are_shuffled(
    client: httpx.AsyncClient, ready_doc: str, quiz_llm: Any
) -> None:
    quiz_llm.generated = questions(8)  # correct answer is always OPTS[0] from the "LLM"
    import app.core.dependencies  # noqa: F401  (service built per request)

    quiz = (await create(client, ready_doc, number_of_questions=8)).json()
    positions = [q["options"].index(OPTS[0]) for q in quiz["questions"]]
    assert all(sorted(q["options"]) == sorted(OPTS) for q in quiz["questions"])
    assert len(set(positions)) > 1  # not always first (8 shuffles: P(all same) ~ 1e-4)


def test_shuffle_options_keeps_answer() -> None:
    import random

    from app.services.quiz import shuffle_options

    q = questions(1)[0]
    shuffled = shuffle_options(q, random.Random(3))
    assert sorted(shuffled.options) == sorted(q.options)
    assert shuffled.correct_answer in shuffled.options
    assert shuffle_options(questions(1, short=True)[0], random.Random(1)).options == []
