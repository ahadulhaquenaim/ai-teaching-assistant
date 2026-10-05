"""Tests for the quiz LangGraph: selection, validation, regenerate-only-invalid, retries."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from app.graphs.quiz_graph import QuizGenerationError, QuizGraph, check_question, sample_indices
from app.schemas.quiz import (
    Difficulty,
    GeneratedQuestion,
    GeneratedQuiz,
    QuestionType,
    SupportCheck,
)
from app.services.llm import LLMProviderError
from app.services.vectorstore import ChunkRecord

from tests.conftest import FakeEmbedder, FakeLLM, FakeVectorStore

DOC = "doc-q"
OPTS = ["Passing dependencies in", "Global variables", "Inheritance", "Monkey patching"]


def mcq(text: str, page: int = 1, **overrides: Any) -> GeneratedQuestion:
    data: dict[str, Any] = {
        "question": text,
        "options": OPTS,
        "correct_answer": OPTS[0],
        "explanation": "The text says so.",
        "source_page": page,
    }
    data.update(overrides)
    return GeneratedQuestion(**data)


class ScriptedQuizLLM(FakeLLM):
    """Returns pre-scripted generation rounds; support check configurable."""

    def __init__(
        self, rounds: list[list[GeneratedQuestion]], unsupported: set[str] | None = None
    ) -> None:
        super().__init__()
        self._rounds: Iterator[list[GeneratedQuestion]] = iter(rounds)
        self.unsupported = unsupported or set()
        self.generate_prompts: list[str] = []
        self.support_fails = False

        def structured(messages: Any, schema: Any) -> Any:
            if schema is GeneratedQuiz:
                self.generate_prompts.append(messages[1].content)
                return GeneratedQuiz(questions=next(self._rounds, []))
            if schema is SupportCheck:
                if self.support_fails:
                    raise LLMProviderError("down")
                body = messages[1].content
                blocks = body.split('<question id="')[1:]
                ids = [
                    int(b.split('"')[0])
                    for b in blocks
                    if not any(u in b for u in self.unsupported)
                ]
                return SupportCheck(supported_ids=ids)
            raise AssertionError(schema)

        self.structured_responder = structured


async def make_graph(
    llm: FakeLLM, pages: int = 4
) -> tuple[QuizGraph, FakeEmbedder, FakeVectorStore]:
    embedder, store = FakeEmbedder(), FakeVectorStore()
    records = [
        ChunkRecord(i, i + 1, f"Page {i + 1} text about dependency injection.")
        for i in range(pages)
    ]
    await store.upsert_chunks(DOC, records, [[0.0]] * len(records))
    return QuizGraph(llm, embedder, store, context_chunks=4, max_retries=2), embedder, store


async def run(
    graph: QuizGraph, n: int = 3, topic: str | None = None, qtype: QuestionType = QuestionType.MCQ
):
    return await graph.run(
        document_id=DOC,
        chunk_count=4,
        topic=topic,
        difficulty=Difficulty.MEDIUM,
        number_of_questions=n,
        question_type=qtype,
    )


# ------------------------------------------------------------------- happy
async def test_generates_requested_questions_from_sampled_content() -> None:
    llm = ScriptedQuizLLM(
        [[mcq("What is DI exactly?"), mcq("Why use DI at all?", 2), mcq("Where is DI used?", 3)]]
    )
    graph, embedder, _ = await make_graph(llm)

    questions = await run(graph)

    assert [q.question for q in questions] == [
        "What is DI exactly?",
        "Why use DI at all?",
        "Where is DI used?",
    ]
    assert embedder.queries == []  # no topic -> sampled, not searched
    assert "[Page 1]" in llm.generate_prompts[0] and "[Page 4]" in llm.generate_prompts[0]


async def test_topic_uses_similarity_search() -> None:
    llm = ScriptedQuizLLM([[mcq("What is DI exactly?")]])
    graph, embedder, store = await make_graph(llm)
    await run(graph, n=1, topic="dependency injection")
    assert embedder.queries == ["dependency injection"]
    assert store.queried == [DOC]
    assert "Focus on this topic: dependency injection" in llm.generate_prompts[0]


# -------------------------------------------------------------- validation
async def test_regenerates_only_invalid_questions() -> None:
    first = [
        mcq("What is DI exactly?"),
        mcq("Three options only here?", options=OPTS[:3]),
        mcq("Answer not in the options?", correct_answer="Something else"),
    ]
    second = [mcq("Why use DI at all?", 2), mcq("Where is DI used?", 3)]
    llm = ScriptedQuizLLM([first, second])
    graph, _, _ = await make_graph(llm)

    questions = await run(graph)

    assert len(questions) == 3
    assert questions[0].question == "What is DI exactly?"  # kept from round 1
    regen_prompt = llm.generate_prompts[1]
    assert "Write 2 question(s)" in regen_prompt  # only the missing ones
    assert "What is DI exactly?" in regen_prompt  # told not to repeat valid ones
    assert "exactly 4 non-empty options" in regen_prompt  # told what went wrong


async def test_unsupported_answers_are_rejected_and_regenerated() -> None:
    llm = ScriptedQuizLLM(
        [
            [mcq("Unsupported claim question?"), mcq("What is DI exactly?")],
            [mcq("Why use DI at all?", 2)],
        ],
        unsupported={"Unsupported claim question?"},
    )
    graph, _, _ = await make_graph(llm)
    questions = await run(graph, n=2)
    assert [q.question for q in questions] == ["What is DI exactly?", "Why use DI at all?"]


async def test_max_two_retries_then_error() -> None:
    bad = [mcq("Bad page question?", page=99)]
    llm = ScriptedQuizLLM([bad, bad, bad, bad])
    graph, _, _ = await make_graph(llm)
    with pytest.raises(QuizGenerationError):
        await run(graph, n=1)
    assert len(llm.generate_prompts) == 3  # initial + 2 regenerations


async def test_partial_result_after_retries() -> None:
    llm = ScriptedQuizLLM([[mcq("What is DI exactly?")], [], []])
    graph, _, _ = await make_graph(llm)
    questions = await run(graph, n=3)
    assert len(questions) == 1


async def test_support_check_failure_fails_open() -> None:
    llm = ScriptedQuizLLM([[mcq("What is DI exactly?")]])
    llm.support_fails = True
    graph, _, _ = await make_graph(llm)
    assert len(await run(graph, n=1)) == 1


async def test_short_answer_questions() -> None:
    q = GeneratedQuestion(
        question="Explain dependency injection.",
        options=["ignored"],
        correct_answer="Passing dependencies in from outside.",
        explanation="Page 1 defines it.",
        source_page=1,
    )
    llm = ScriptedQuizLLM([[q]])
    graph, _, _ = await make_graph(llm)
    [result] = await run(graph, n=1, qtype=QuestionType.SHORT_ANSWER)
    assert result.options == []


# ------------------------------------------------------- deterministic check
@pytest.mark.parametrize(
    ("question", "reason"),
    [
        (mcq("Short?"), "too short"),
        (mcq("Wrong page question?", page=9), "source_page"),
        (mcq("Three options question?", options=OPTS[:3]), "exactly 4"),
        (
            mcq("Duplicate options question?", options=["a", "a", "b", "c"], correct_answer="b"),
            "distinct",
        ),
        (mcq("All of the above question?", options=[*OPTS[:3], "All of the above"]), "all/none"),
        (
            mcq(
                "Two correct answers question?",
                options=["Yes", "yes", "No", "Maybe"],
                correct_answer="yes",
            ),
            "distinct",
        ),
        (mcq("No explanation question?", explanation="  "), "explanation"),
    ],
)
def test_check_question_rejects(question: GeneratedQuestion, reason: str) -> None:
    cleaned, why = check_question(question, QuestionType.MCQ, pages={1, 2}, seen=set())
    assert cleaned is None
    assert reason in (why or "")


def test_check_question_maps_letter_and_rejects_duplicates() -> None:
    seen: set[str] = set()
    cleaned, _ = check_question(
        mcq("What is DI exactly?", correct_answer="a"), QuestionType.MCQ, {1}, seen
    )
    assert cleaned is not None and cleaned.correct_answer == OPTS[0]
    again, why = check_question(mcq("what is DI, exactly"), QuestionType.MCQ, {1}, seen)
    assert again is None and "duplicate" in (why or "")


def test_sample_indices_spread_across_document() -> None:
    assert sample_indices(100, 4) == [0, 25, 50, 75]
    assert sample_indices(3, 10) == [0, 1, 2]
    assert sample_indices(0, 5) == []
