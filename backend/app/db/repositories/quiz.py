"""Repositories for `quizzes` and `quiz_attempts` (all reads filtered by user)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pymongo import DESCENDING

from app.db.repositories.documents import MongoDoc, parse_object_id
from app.schemas.quiz import AttemptOut, QuestionOut, QuizOut, QuizSummary


def to_quiz_summary(doc: MongoDoc) -> QuizSummary:
    return QuizSummary(
        id=str(doc["_id"]),
        document_id=doc["document_id"],
        topic=doc.get("topic"),
        difficulty=doc["difficulty"],
        question_type=doc["question_type"],
        question_count=len(doc["questions"]),
        created_at=doc["created_at"],
    )


def to_attempt_out(doc: MongoDoc) -> AttemptOut:
    return AttemptOut(
        id=str(doc["_id"]),
        quiz_id=doc["quiz_id"],
        score=doc["score"],
        total=doc["total"],
        percentage=doc["percentage"],
        results=doc["feedback"],
        created_at=doc["created_at"],
    )


def to_quiz_out(doc: MongoDoc, attempts: list[MongoDoc]) -> QuizOut:
    """Quiz for taking: correct answers/explanations are NOT included.

    They are revealed per question in attempt results after submitting.
    """
    return QuizOut(
        **to_quiz_summary(doc).model_dump(),
        questions=[
            QuestionOut(id=q["id"], question=q["question"], options=q["options"], source_page=q["source_page"])
            for q in doc["questions"]
        ],
        attempts=[to_attempt_out(a) for a in attempts],
    )


class QuizRepository:
    def __init__(self, db: Any) -> None:
        self._col = db["quizzes"]

    async def create(
        self,
        *,
        user_id: str,
        document_id: str,
        topic: str | None,
        difficulty: str,
        question_type: str,
        questions: list[dict[str, Any]],
        requested_count: int,
    ) -> MongoDoc:
        doc: MongoDoc = {
            "user_id": user_id,
            "document_id": document_id,
            "topic": topic,
            "difficulty": difficulty,
            "question_type": question_type,
            "questions": questions,
            "requested_count": requested_count,
            "created_at": datetime.now(UTC),
        }
        result = await self._col.insert_one(doc)
        doc["_id"] = result.inserted_id
        return doc

    async def get_owned(self, quiz_id: str, user_id: str) -> MongoDoc | None:
        oid = parse_object_id(quiz_id)
        if oid is None:
            return None
        return await self._col.find_one({"_id": oid, "user_id": user_id})

    async def list_for_user(self, user_id: str, document_id: str | None = None) -> list[MongoDoc]:
        query: MongoDoc = {"user_id": user_id}
        if document_id is not None:
            query["document_id"] = document_id
        return await self._col.find(query).sort("created_at", DESCENDING).to_list(length=None)


class QuizAttemptRepository:
    def __init__(self, db: Any) -> None:
        self._col = db["quiz_attempts"]

    async def create(
        self,
        *,
        quiz_id: str,
        user_id: str,
        answers: list[dict[str, Any]],
        score: float,
        total: int,
        percentage: float,
        feedback: list[dict[str, Any]],
    ) -> MongoDoc:
        doc: MongoDoc = {
            "quiz_id": quiz_id,
            "user_id": user_id,
            "answers": answers,
            "score": score,
            "total": total,
            "percentage": percentage,
            "feedback": feedback,  # per-question results
            "created_at": datetime.now(UTC),
        }
        result = await self._col.insert_one(doc)
        doc["_id"] = result.inserted_id
        return doc

    async def list_for_quiz(self, quiz_id: str, user_id: str) -> list[MongoDoc]:
        return await self._col.find({"quiz_id": quiz_id, "user_id": user_id}).sort(
            "created_at", DESCENDING
        ).to_list(length=None)
