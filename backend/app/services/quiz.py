"""Quiz use-cases: generate (via QuizGraph), list/get, submit + score."""

from __future__ import annotations

import logging
import random
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.config import Settings
from app.core.errors import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    UnprocessableError,
)
from app.db.repositories.documents import DocumentRepository, MongoDoc
from app.db.repositories.quiz import QuizAttemptRepository, QuizRepository
from app.graphs.quiz_graph import QuizGenerationError, QuizGraph, normalize
from app.prompts.quiz import GRADE_SHORT_SYSTEM_PROMPT, GRADE_SHORT_USER_TEMPLATE
from app.schemas.document import DocumentStatus
from app.schemas.quiz import (
    GeneratedQuestion,
    QuestionType,
    QuizCreate,
    ShortAnswerGrades,
    SubmittedAnswer,
)
from app.services.llm import LLM, LLMError, LLMRateLimitError

logger = logging.getLogger(__name__)


def shuffle_options(question: GeneratedQuestion, rng: random.Random) -> GeneratedQuestion:
    """LLMs tend to put the correct option first; shuffle so position gives nothing away."""
    if not question.options:
        return question
    options = list(question.options)
    rng.shuffle(options)
    return question.model_copy(update={"options": options})


def score_mcq(question: dict[str, Any], answer: str) -> dict[str, Any]:
    """Exact scoring: the chosen option must equal the correct option."""
    correct = bool(answer) and normalize(answer) == normalize(question["correct_answer"])
    if correct:
        feedback = "Correct!"
    elif answer:
        feedback = f"Incorrect. The correct answer is: {question['correct_answer']}"
    else:
        feedback = f"No answer given. The correct answer is: {question['correct_answer']}"
    return {"score": 1.0 if correct else 0.0, "is_correct": correct, "feedback": feedback}


class QuizService:
    def __init__(
        self,
        settings: Settings,
        quizzes: QuizRepository,
        attempts: QuizAttemptRepository,
        documents: DocumentRepository,
        graph: QuizGraph,
        llm: LLM,
        rng: random.Random | None = None,
    ) -> None:
        self._settings = settings
        self._quizzes = quizzes
        self._attempts = attempts
        self._documents = documents
        self._graph = graph
        self._llm = llm
        self._rng = rng or random.Random()

    # ------------------------------------------------------------ create
    async def create(self, user_id: str, req: QuizCreate) -> MongoDoc:
        if req.number_of_questions > self._settings.quiz_max_questions:
            raise UnprocessableError(
                f"You can generate at most {self._settings.quiz_max_questions} questions at a time."
            )
        doc = await self._documents.get_owned(req.document_id, user_id)
        if doc is None:
            raise NotFoundError("Document not found.")
        if doc["status"] != DocumentStatus.READY.value:
            raise ConflictError("The document is not ready yet.")

        try:
            questions = await self._graph.run(
                document_id=req.document_id,
                chunk_count=doc.get("chunk_count") or 0,
                topic=req.topic,
                difficulty=req.difficulty,
                number_of_questions=req.number_of_questions,
                question_type=req.question_type,
            )
        except QuizGenerationError as exc:
            raise UnprocessableError(str(exc)) from exc
        except LLMRateLimitError as exc:
            raise ServiceUnavailableError(str(exc)) from exc
        except LLMError as exc:
            raise ServiceUnavailableError(
                "The AI model could not generate the quiz right now. Please try again."
            ) from exc
        except Exception as exc:
            logger.exception("quiz generation failed")
            raise ServiceUnavailableError("Quiz generation is temporarily unavailable.") from exc

        stored = [
            {"id": i, **shuffle_options(q, self._rng).model_dump()} for i, q in enumerate(questions)
        ]
        quiz = await self._quizzes.create(
            user_id=user_id,
            document_id=req.document_id,
            topic=req.topic or None,
            difficulty=req.difficulty.value,
            question_type=req.question_type.value,
            questions=stored,
            requested_count=req.number_of_questions,
        )
        logger.info(
            "quiz created",
            extra={
                "quiz_id": str(quiz["_id"]),
                "requested": req.number_of_questions,
                "generated": len(stored),
            },
        )
        return quiz

    # -------------------------------------------------------------- read
    async def get(self, user_id: str, quiz_id: str) -> tuple[MongoDoc, list[MongoDoc]]:
        quiz = await self._quizzes.get_owned(quiz_id, user_id)
        if quiz is None:
            raise NotFoundError("Quiz not found.")
        return quiz, await self._attempts.list_for_quiz(quiz_id, user_id)

    async def list(self, user_id: str, document_id: str | None) -> list[MongoDoc]:
        return await self._quizzes.list_for_user(user_id, document_id)

    # ------------------------------------------------------------ submit
    async def submit(self, user_id: str, quiz_id: str, answers: list[SubmittedAnswer]) -> MongoDoc:
        quiz = await self._quizzes.get_owned(quiz_id, user_id)
        if quiz is None:
            raise NotFoundError("Quiz not found.")
        questions = {q["id"]: q for q in quiz["questions"]}
        unknown = sorted({a.question_id for a in answers} - questions.keys())
        if unknown:
            raise UnprocessableError(f"Unknown question ids: {unknown}")
        given = {a.question_id: a.answer for a in answers}

        if quiz["question_type"] == QuestionType.MCQ.value:
            graded = {qid: score_mcq(q, given.get(qid, "")) for qid, q in questions.items()}
        else:
            graded = await self._grade_short_answers(questions, given)

        results = [
            {
                "question_id": qid,
                "question": q["question"],
                "user_answer": given.get(qid, ""),
                "correct_answer": q["correct_answer"],
                "is_correct": graded[qid]["is_correct"],
                "score": graded[qid]["score"],
                "feedback": graded[qid]["feedback"],
                "explanation": q["explanation"],
                "source_page": q["source_page"],
            }
            for qid, q in sorted(questions.items())
        ]
        score = round(sum(r["score"] for r in results), 2)
        total = len(results)
        return await self._attempts.create(
            quiz_id=quiz_id,
            user_id=user_id,
            answers=[
                {"question_id": qid, "answer": given.get(qid, "")} for qid in sorted(questions)
            ],
            score=score,
            total=total,
            percentage=round(100 * score / total, 1) if total else 0.0,
            feedback=results,
        )

    async def _grade_short_answers(
        self, questions: dict[int, dict[str, Any]], given: dict[int, str]
    ) -> dict[int, dict[str, Any]]:
        """One LLM call grades every non-empty answer against its reference answer."""
        graded: dict[int, dict[str, Any]] = {
            qid: {"score": 0.0, "is_correct": False, "feedback": "No answer given."}
            for qid in questions
            if not given.get(qid)
        }
        to_grade = [qid for qid in questions if given.get(qid)]
        if not to_grade:
            return graded

        items = "\n\n".join(
            f'<item question_id="{qid}">\nQuestion: {questions[qid]["question"]}\n'
            f"Reference answer: {questions[qid]['correct_answer']}\n"
            f"<student_answer>{given[qid]}</student_answer>\n</item>"
            for qid in to_grade
        )
        try:
            result = await self._llm.generate_structured(
                [
                    SystemMessage(GRADE_SHORT_SYSTEM_PROMPT),
                    HumanMessage(GRADE_SHORT_USER_TEMPLATE.format(items=items)),
                ],
                ShortAnswerGrades,
            )
        except LLMRateLimitError as exc:
            raise ServiceUnavailableError(str(exc)) from exc
        except LLMError as exc:
            raise ServiceUnavailableError(
                "Answers could not be graded right now. Please try again."
            ) from exc

        by_id = {g.question_id: g for g in result.grades}
        for qid in to_grade:
            grade = by_id.get(qid)
            if grade is None:
                raise ServiceUnavailableError(
                    "Answers could not be graded right now. Please try again."
                )
            score = round(min(1.0, max(0.0, grade.score)), 2)
            graded[qid] = {
                "score": score,
                "is_correct": score >= 0.7,
                "feedback": grade.feedback.strip(),
            }
        return graded
