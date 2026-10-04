"""Pydantic schemas for quizzes, attempts, and LLM structured output."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class QuestionType(StrEnum):
    MCQ = "mcq"
    SHORT_ANSWER = "short_answer"


# ----------------------------------------------------------- LLM structured
class GeneratedQuestion(BaseModel):
    """One question as produced by the LLM (validated afterwards)."""

    question: str = Field(description="The question text")
    options: list[str] = Field(
        default_factory=list,
        description="Exactly 4 answer options for MCQ; empty list for short answer",
    )
    correct_answer: str = Field(
        description="For MCQ: the exact text of the correct option. For short answer: a model answer"
    )
    explanation: str = Field(description="Why the answer is correct, based on the source text")
    source_page: int = Field(description="Page number of the excerpt the question is based on")


class GeneratedQuiz(BaseModel):
    questions: list[GeneratedQuestion] = Field(default_factory=list)


class SupportCheck(BaseModel):
    supported_ids: list[int] = Field(
        default_factory=list, description="Ids of questions whose answer is supported by its source"
    )


class ShortAnswerGrade(BaseModel):
    question_id: int
    score: float = Field(ge=0.0, le=1.0, description="0 = wrong, 1 = fully correct, partial allowed")
    feedback: str = Field(description="One or two sentences of feedback for the student")


class ShortAnswerGrades(BaseModel):
    grades: list[ShortAnswerGrade] = Field(default_factory=list)


# ----------------------------------------------------------------- requests
class QuizCreate(BaseModel):
    document_id: str = Field(min_length=1)
    topic: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    difficulty: Difficulty = Difficulty.MEDIUM
    number_of_questions: int = Field(default=5, ge=1, le=20)
    question_type: QuestionType = QuestionType.MCQ


class SubmittedAnswer(BaseModel):
    question_id: int = Field(ge=0)
    answer: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""


class QuizSubmit(BaseModel):
    answers: list[SubmittedAnswer] = Field(max_length=50)


# ---------------------------------------------------------------- responses
class QuestionOut(BaseModel):
    """A question as shown while taking the quiz (no answer revealed)."""

    id: int
    question: str
    options: list[str]
    source_page: int


class QuestionResult(BaseModel):
    question_id: int
    question: str
    user_answer: str
    correct_answer: str
    is_correct: bool
    score: float
    feedback: str
    explanation: str
    source_page: int


class AttemptOut(BaseModel):
    id: str
    quiz_id: str
    score: float  # points earned (partial credit for short answers)
    total: int
    percentage: float
    results: list[QuestionResult]
    created_at: datetime


class QuizSummary(BaseModel):
    id: str
    document_id: str
    topic: str | None
    difficulty: Difficulty
    question_type: QuestionType
    question_count: int
    created_at: datetime


class QuizOut(QuizSummary):
    questions: list[QuestionOut]
    attempts: list[AttemptOut]


class QuizListResponse(BaseModel):
    quizzes: list[QuizSummary]
