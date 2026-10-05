"""Quiz LangGraph: select content -> generate -> validate -> regenerate invalid only.

    START -> select_content -> generate_questions -> validate
          -> (not enough valid and retries left) generate_questions   [only the missing ones]
          -> END

Validation is two-stage:
1. Deterministic checks (cheap, no LLM): format, MCQ has exactly 4 distinct options,
   exactly one correct answer, no duplicates, source page exists in the context.
2. One batched LLM call checks that each answer is supported by its source excerpt.

SECURITY: `document_id` must come from an ownership-checked lookup.
"""

from __future__ import annotations

import logging
import re
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage

from app.prompts.quiz import (
    DIFFICULTY_GUIDE,
    GENERATE_SYSTEM_PROMPT,
    GENERATE_USER_TEMPLATE,
    SUPPORT_SYSTEM_PROMPT,
    SUPPORT_USER_TEMPLATE,
    TYPE_RULES,
)
from app.schemas.quiz import (
    Difficulty,
    GeneratedQuestion,
    GeneratedQuiz,
    QuestionType,
    SupportCheck,
)
from app.services.embeddings import Embedder
from app.services.llm import LLM, LLMError
from app.services.vectorstore import RetrievedChunk, VectorStore

logger = logging.getLogger(__name__)

_MCQ_OPTIONS = 4
_SOURCE_EXCERPT_CHARS = 1500
_BANNED_OPTIONS = {"all of the above", "none of the above"}


class QuizGenerationError(Exception):
    """User-facing failure (e.g. no usable content or no valid questions)."""


class QuizState(TypedDict, total=False):
    document_id: str
    chunk_count: int
    topic: str | None
    difficulty: Difficulty
    number_of_questions: int
    question_type: QuestionType
    chunks: list[RetrievedChunk]
    candidates: list[GeneratedQuestion]
    valid_questions: list[GeneratedQuestion]
    rejection_reasons: list[str]
    rounds: int  # generation rounds so far (1 = first attempt)


# ----------------------------------------------------------------- helpers
def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def sample_indices(chunk_count: int, k: int) -> list[int]:
    """`k` chunk indices spread evenly across the whole document."""
    if chunk_count <= 0:
        return []
    k = min(k, chunk_count)
    return sorted({int(i * chunk_count / k) for i in range(k)})


def format_excerpts(chunks: list[RetrievedChunk], max_chars: int) -> str:
    parts: list[str] = []
    used = 0
    for c in sorted(chunks, key=lambda c: (c.page_number, c.chunk_index)):
        piece = f"[Page {c.page_number}]\n{c.text}"
        if used + len(piece) > max_chars and parts:
            break
        parts.append(piece)
        used += len(piece)
    return "\n\n".join(parts)


def page_text(chunks: list[RetrievedChunk], page: int) -> str:
    text = "\n".join(c.text for c in chunks if c.page_number == page)
    return text[:_SOURCE_EXCERPT_CHARS]


def check_question(
    q: GeneratedQuestion, question_type: QuestionType, pages: set[int], seen: set[str]
) -> tuple[GeneratedQuestion | None, str | None]:
    """Deterministic validation. Returns (cleaned question, None) or (None, reason)."""
    question = q.question.strip()
    if len(question) < 8:
        return None, "question text is missing or too short"
    if q.source_page not in pages:
        return None, f"source_page {q.source_page} is not one of the provided excerpt pages"
    if not q.explanation.strip():
        return None, "explanation is missing"
    key = normalize(question)
    if key in seen:
        return None, f"duplicate question: {question[:80]}"

    if question_type is QuestionType.MCQ:
        options = [o.strip() for o in q.options]
        if len(options) != _MCQ_OPTIONS or any(not o for o in options):
            return None, "MCQ must have exactly 4 non-empty options"
        if len({normalize(o) for o in options}) != _MCQ_OPTIONS:
            return None, "MCQ options must be distinct"
        if any(normalize(o) in _BANNED_OPTIONS for o in options):
            return None, "do not use 'all/none of the above'"
        answer = q.correct_answer.strip()
        # Accept a bare letter ("B") and map it to the option text.
        if len(answer) == 1 and answer.upper() in "ABCD":
            answer = options["ABCD".index(answer.upper())]
        matches = [o for o in options if normalize(o) == normalize(answer)]
        if len(matches) != 1:
            return None, "correct_answer must match exactly one option"
        cleaned = q.model_copy(
            update={"question": question, "options": options, "correct_answer": matches[0]}
        )
    else:
        if not q.correct_answer.strip():
            return None, "model answer is missing"
        cleaned = q.model_copy(
            update={"question": question, "options": [], "correct_answer": q.correct_answer.strip()}
        )
    seen.add(key)
    return cleaned, None


# ------------------------------------------------------------------- graph
class QuizGraph:
    def __init__(
        self,
        llm: LLM,
        embedder: Embedder,
        vector_store: VectorStore,
        *,
        context_chunks: int = 12,
        context_max_chars: int = 14_000,
        max_retries: int = 2,
    ) -> None:
        self._llm = llm
        self._embedder = embedder
        self._vectors = vector_store
        self._context_chunks = context_chunks
        self._context_max_chars = context_max_chars
        self._max_retries = max_retries
        self._graph = self._build()

    def _build(self) -> Any:
        from langgraph.graph import END, START, StateGraph

        graph = StateGraph(QuizState)
        graph.add_node("select_content", self.select_content)
        graph.add_node("generate_questions", self.generate_questions)
        graph.add_node("validate", self.validate)
        graph.add_edge(START, "select_content")
        graph.add_edge("select_content", "generate_questions")
        graph.add_edge("generate_questions", "validate")
        graph.add_conditional_edges(
            "validate",
            self.route_after_validation,
            {"regenerate": "generate_questions", "done": END},
        )
        return graph.compile()

    async def run(
        self,
        *,
        document_id: str,
        chunk_count: int,
        topic: str | None,
        difficulty: Difficulty,
        number_of_questions: int,
        question_type: QuestionType,
    ) -> list[GeneratedQuestion]:
        final: QuizState = await self._graph.ainvoke(
            {
                "document_id": document_id,
                "chunk_count": chunk_count,
                "topic": topic or None,
                "difficulty": difficulty,
                "number_of_questions": number_of_questions,
                "question_type": question_type,
                "valid_questions": [],
                "rejection_reasons": [],
                "rounds": 0,
            },
            # select + (generate + validate) * (1 + retries) nodes, with headroom.
            config={"recursion_limit": 10 + 4 * self._max_retries},
        )
        questions = final.get("valid_questions", [])[:number_of_questions]
        if not questions:
            raise QuizGenerationError(
                "Could not generate valid questions from this document. Try another topic or difficulty."
            )
        return questions

    # ------------------------------------------------------------- nodes
    async def select_content(self, state: QuizState) -> QuizState:
        """Topic -> similarity search; otherwise sample chunks across the document."""
        chunks: list[RetrievedChunk] = []
        topic = state.get("topic")
        if topic:
            vector = await self._embedder.embed_query(topic)
            chunks = await self._vectors.query(state["document_id"], vector, self._context_chunks)
        if not chunks:
            indices = sample_indices(state.get("chunk_count", 0), self._context_chunks)
            chunks = await self._vectors.fetch_chunks(state["document_id"], indices)
        if not chunks:
            raise QuizGenerationError("No content is available for this document.")
        logger.info("quiz content selected", extra={"chunks": len(chunks), "by_topic": bool(topic)})
        return {"chunks": chunks}

    async def generate_questions(self, state: QuizState) -> QuizState:
        """Generate only the number of questions still missing."""
        valid = state.get("valid_questions") or []
        needed = state["number_of_questions"] - len(valid)
        qtype = state["question_type"]
        avoid = "\n".join(f"- {q.question}" for q in valid)
        reasons = state.get("rejection_reasons") or []
        prompt = GENERATE_USER_TEMPLATE.format(
            count=needed,
            difficulty=state["difficulty"].value,
            difficulty_guide=DIFFICULTY_GUIDE[state["difficulty"].value],
            type_rules=TYPE_RULES[qtype.value],
            topic_line=f"Focus on this topic: {state['topic']}\n" if state.get("topic") else "",
            avoid_block=f"\nExisting questions (do not repeat):\n{avoid}\n" if avoid else "",
            feedback_block=(
                "\nPrevious attempt problems to avoid:\n"
                + "\n".join(f"- {r}" for r in reasons[-8:])
                + "\n"
                if reasons
                else ""
            ),
            excerpts=format_excerpts(state["chunks"], self._context_max_chars),
        )
        result = await self._llm.generate_structured(
            [SystemMessage(GENERATE_SYSTEM_PROMPT), HumanMessage(prompt)], GeneratedQuiz
        )
        rounds = state.get("rounds", 0) + 1
        logger.info(
            "quiz questions generated",
            extra={"needed": needed, "got": len(result.questions), "round": rounds},
        )
        return {"candidates": result.questions[: needed + 2], "rounds": rounds}

    async def validate(self, state: QuizState) -> QuizState:
        valid = list(state.get("valid_questions") or [])
        chunks = state["chunks"]
        pages = {c.page_number for c in chunks}
        seen = {normalize(q.question) for q in valid}
        reasons: list[str] = []

        passed: list[GeneratedQuestion] = []
        for candidate in state.get("candidates") or []:
            cleaned, reason = check_question(candidate, state["question_type"], pages, seen)
            if cleaned is None:
                reasons.append(reason or "invalid")
            else:
                passed.append(cleaned)

        supported = await self._check_support(passed, chunks)
        for i, q in enumerate(passed, start=1):
            if i in supported:
                valid.append(q)
            else:
                reasons.append(f"answer not supported by page {q.source_page}: {q.question[:80]}")

        logger.info(
            "quiz questions validated",
            extra={
                "valid_total": len(valid),
                "rejected": len(reasons),
                "round": state.get("rounds", 0),
            },
        )
        return {"valid_questions": valid, "rejection_reasons": reasons, "candidates": []}

    def route_after_validation(self, state: QuizState) -> str:
        enough = len(state.get("valid_questions") or []) >= state["number_of_questions"]
        # rounds = 1 initial generation + up to `max_retries` regenerations.
        if enough or state.get("rounds", 0) > self._max_retries:
            return "done"
        return "regenerate"

    async def _check_support(
        self, questions: list[GeneratedQuestion], chunks: list[RetrievedChunk]
    ) -> set[int]:
        """Ids (1-based) of questions whose answer the LLM finds in the source excerpt."""
        if not questions:
            return set()
        items = "\n\n".join(
            f'<question id="{i}">\nQuestion: {q.question}\nCorrect answer: {q.correct_answer}\n'
            f"Source excerpt (page {q.source_page}):\n{page_text(chunks, q.source_page)}\n</question>"
            for i, q in enumerate(questions, start=1)
        )
        try:
            result = await self._llm.generate_structured(
                [
                    SystemMessage(SUPPORT_SYSTEM_PROMPT),
                    HumanMessage(SUPPORT_USER_TEMPLATE.format(questions=items)),
                ],
                SupportCheck,
            )
        except LLMError:
            # Fail open: deterministic checks already passed.
            logger.warning("quiz support check failed; keeping deterministic-valid questions")
            return set(range(1, len(questions) + 1))
        return {i for i in result.supported_ids if 1 <= i <= len(questions)}
