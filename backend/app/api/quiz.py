"""Quiz endpoints: generate, list, get (for taking), submit (scored)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.dependencies import CurrentUserDep, get_quiz_service
from app.db.repositories.quiz import to_attempt_out, to_quiz_out, to_quiz_summary
from app.schemas.quiz import AttemptOut, QuizCreate, QuizListResponse, QuizOut, QuizSubmit
from app.services.quiz import QuizService

router = APIRouter(prefix="/quizzes", tags=["quizzes"])

QuizServiceDep = Annotated[QuizService, Depends(get_quiz_service)]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=QuizOut)
async def create_quiz(body: QuizCreate, user: CurrentUserDep, service: QuizServiceDep) -> QuizOut:
    """Generate a quiz from a ready document (may take 20-60 s on free-tier LLMs)."""
    quiz = await service.create(user.user_id, body)
    return to_quiz_out(quiz, [])


@router.get("", response_model=QuizListResponse)
async def list_quizzes(
    user: CurrentUserDep,
    service: QuizServiceDep,
    document_id: Annotated[str | None, Query()] = None,
) -> QuizListResponse:
    quizzes = await service.list(user.user_id, document_id)
    return QuizListResponse(quizzes=[to_quiz_summary(q) for q in quizzes])


@router.get("/{quiz_id}", response_model=QuizOut)
async def get_quiz(quiz_id: str, user: CurrentUserDep, service: QuizServiceDep) -> QuizOut:
    """Questions without answers, plus past attempts (which reveal answers and explanations)."""
    quiz, attempts = await service.get(user.user_id, quiz_id)
    return to_quiz_out(quiz, attempts)


@router.post("/{quiz_id}/submit", response_model=AttemptOut)
async def submit_quiz(
    quiz_id: str, body: QuizSubmit, user: CurrentUserDep, service: QuizServiceDep
) -> AttemptOut:
    """Score answers: MCQ exactly, short answers with the LLM (with feedback)."""
    return to_attempt_out(await service.submit(user.user_id, quiz_id, body.answers))
