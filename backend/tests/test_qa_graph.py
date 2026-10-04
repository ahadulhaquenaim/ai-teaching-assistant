"""Tests for the Q&A LangGraph with fake LLM, embedder, and vector store."""

from __future__ import annotations

from typing import Any

import pytest

from app.graphs.qa_graph import (
    ChatTurn,
    GradeResult,
    QAGraph,
    extract_cited_pages,
    is_not_covered,
)
from app.prompts.qa import (
    ANSWER_SYSTEM_PROMPT,
    NOT_COVERED_ANSWER,
    REPHRASE_SYSTEM_PROMPT,
    REWRITE_SYSTEM_PROMPT,
)
from app.services.vectorstore import ChunkRecord
from tests.conftest import FakeEmbedder, FakeLLM, FakeVectorStore

DOC = "doc-1"


async def make_graph(
    chunks: list[tuple[int, str]] | None = None,
) -> tuple[QAGraph, FakeLLM, FakeEmbedder, FakeVectorStore]:
    llm, embedder, store = FakeLLM(), FakeEmbedder(), FakeVectorStore()
    chunks = chunks or [(1, "DI means passing dependencies in."), (3, "FastAPI uses Depends.")]
    records = [ChunkRecord(i, page, text) for i, (page, text) in enumerate(chunks)]
    await store.upsert_chunks(DOC, records, [[0.0]] * len(records))
    return QAGraph(llm, embedder, store, top_k=5, history_messages=4), llm, embedder, store


def system_of(messages: Any) -> str:
    return messages[0].content


def answer_with(text: str, *, rewrite: str = "rewritten q", rephrase: str = "rephrased q"):
    def responder(messages: Any) -> str:
        system = system_of(messages)
        if system == REWRITE_SYSTEM_PROMPT:
            return rewrite
        if system == REPHRASE_SYSTEM_PROMPT:
            return rephrase
        if system == ANSWER_SYSTEM_PROMPT:
            return text
        raise AssertionError("unexpected prompt")

    return responder


async def run(graph: QAGraph, question: str = "What is DI?", history: list[ChatTurn] | None = None):
    return await graph.run(
        question=question, chat_history=history or [], document_id=DOC, document_summary="DI notes"
    )


# ------------------------------------------------------------------ happy path
async def test_answer_with_page_citations() -> None:
    graph, llm, embedder, store = await make_graph()
    llm.responder = answer_with("DI passes dependencies in (Page 1). FastAPI uses Depends (Page 3).")

    result = await run(graph)

    assert "(Page 1)" in result.answer
    assert result.sources == [{"type": "document", "page": 1}, {"type": "document", "page": 3}]
    # First question: no rewrite LLM call, original question is used for retrieval.
    assert all(system_of(c) != REWRITE_SYSTEM_PROMPT for c in llm.calls)
    assert embedder.queries == ["What is DI?"]
    assert store.queried == [DOC]


async def test_answer_context_is_delimited_and_paged() -> None:
    graph, llm, _, _ = await make_graph()
    llm.responder = answer_with("Answer (Page 1).")
    await run(graph)
    answer_call = next(c for c in llm.calls if system_of(c) == ANSWER_SYSTEM_PROMPT)
    assert "<document_context>" in answer_call[1].content
    assert "[Page 1]" in answer_call[1].content


async def test_follow_up_is_rewritten_with_history() -> None:
    graph, llm, embedder, _ = await make_graph()
    llm.responder = answer_with("It decouples code (Page 1).", rewrite="Why is dependency injection useful?")
    history: list[ChatTurn] = [
        {"role": "user", "content": "What is dependency injection?"},
        {"role": "assistant", "content": "Passing dependencies in (Page 1)."},
    ]

    result = await run(graph, "Why is it useful?", history)

    rewrite_call = next(c for c in llm.calls if system_of(c) == REWRITE_SYSTEM_PROMPT)
    assert "What is dependency injection?" in rewrite_call[1].content
    assert embedder.queries == ["Why is dependency injection useful?"]
    assert result.rewritten_question == "Why is dependency injection useful?"


async def test_history_is_trimmed() -> None:
    graph, llm, _, _ = await make_graph()
    llm.responder = answer_with("x (Page 1).")
    history: list[ChatTurn] = [{"role": "user", "content": f"msg {i}"} for i in range(10)]
    await run(graph, "follow up", history)
    rewrite_call = next(c for c in llm.calls if system_of(c) == REWRITE_SYSTEM_PROMPT)
    assert "msg 9" in rewrite_call[1].content
    assert "msg 5" not in rewrite_call[1].content  # history_messages=4 keeps msgs 6-9


# --------------------------------------------------------------- grading/retry
async def test_irrelevant_chunks_are_filtered() -> None:
    graph, llm, _, _ = await make_graph()
    llm.responder = answer_with("FastAPI uses Depends (Page 3).")
    llm.structured_responder = lambda m, s: GradeResult(relevant_ids=[2])

    result = await run(graph)

    answer_call = next(c for c in llm.calls if system_of(c) == ANSWER_SYSTEM_PROMPT)
    assert "[Page 3]" in answer_call[1].content
    assert "[Page 1]" not in answer_call[1].content
    assert result.sources == [{"type": "document", "page": 3}]


async def test_nothing_relevant_retries_once_then_not_covered() -> None:
    graph, llm, embedder, _ = await make_graph()
    llm.responder = answer_with("should not be called")
    llm.structured_responder = lambda m, s: GradeResult(relevant_ids=[])

    result = await run(graph)

    assert result.answer == NOT_COVERED_ANSWER
    assert result.sources == []
    assert embedder.queries == ["What is DI?", "rephrased q"]  # exactly one retry
    assert not any(system_of(c) == ANSWER_SYSTEM_PROMPT for c in llm.calls)  # no guessing


async def test_retry_with_rephrased_query_can_succeed() -> None:
    graph, llm, embedder, _ = await make_graph()
    llm.responder = answer_with("Found it (Page 1).")
    grades = iter([GradeResult(relevant_ids=[]), GradeResult(relevant_ids=[1])])
    llm.structured_responder = lambda m, s: next(grades)

    result = await run(graph)

    assert result.answer == "Found it (Page 1)."
    assert len(embedder.queries) == 2


async def test_grader_failure_fails_open() -> None:
    graph, llm, _, _ = await make_graph()
    llm.responder = answer_with("Answer (Page 1).")

    def broken(m: Any, s: Any) -> Any:
        from app.services.llm import LLMProviderError

        raise LLMProviderError("grader down")

    llm.structured_responder = broken
    result = await run(graph)
    assert result.answer == "Answer (Page 1)."


async def test_model_saying_not_covered_has_no_sources() -> None:
    graph, llm, _, _ = await make_graph()
    llm.responder = answer_with("This is not covered in the document.")
    result = await run(graph)
    assert result.answer == NOT_COVERED_ANSWER
    assert result.sources == []


async def test_empty_namespace_is_not_covered() -> None:
    llm, embedder, store = FakeLLM(), FakeEmbedder(), FakeVectorStore()
    graph = QAGraph(llm, embedder, store)
    result = await graph.run(question="q", chat_history=[], document_id="empty", document_summary=None)
    assert result.answer == NOT_COVERED_ANSWER


async def test_uncited_answer_falls_back_to_context_pages() -> None:
    graph, llm, _, _ = await make_graph()
    llm.responder = answer_with("An answer without explicit citations.")
    result = await run(graph)
    assert result.sources == [{"type": "document", "page": 1}, {"type": "document", "page": 3}]


# -------------------------------------------------------------------- helpers
@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("A (Page 3).", [3]),
        ("A (Pages 3, 5) and B (page 7).", [3, 5, 7]),
        ("See Page 2 and Page 4.", [2, 4]),
        ("Cites (Page 99) which was not retrieved.", []),
        ("No citations here.", []),
    ],
)
def test_extract_cited_pages(answer: str, expected: list[int]) -> None:
    assert extract_cited_pages(answer, allowed={2, 3, 4, 5, 7}) == expected


def test_is_not_covered() -> None:
    assert is_not_covered("This is not covered in the document.")
    assert is_not_covered("  this is not covered in the document ")
    assert not is_not_covered("Partly covered (Page 1).")
