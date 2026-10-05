"""Q&A LangGraph: document-first answers with page citations, optional web context.

    START -> rewrite_query -> retrieve_document -> grade_documents
          -> (nothing relevant and no retry yet) rephrase_query -> retrieve_document
          -> web_search_enabled?
               no : generate_answer
               yes: build_search_query -> web_search -> grade_web_results -> generate_answer
          -> END

The graph depends only on the `LLM`, `Embedder`, `VectorStore` and
`WebSearcher` interfaces, never on a provider SDK.

SECURITY:
- `document_id` must come from an ownership-checked lookup; the graph queries
  exactly that Pinecone namespace and nothing else.
- Web content is untrusted: it is sanitized by the web search service and
  only ever placed inside <untrusted_web_content> with explicit instructions
  to treat it as reference data.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.prompts.qa import (
    ANSWER_SYSTEM_PROMPT,
    ANSWER_USER_TEMPLATE,
    GRADE_SYSTEM_PROMPT,
    GRADE_USER_TEMPLATE,
    NOT_COVERED_ANSWER,
    REPHRASE_SYSTEM_PROMPT,
    REPHRASE_USER_TEMPLATE,
    REWRITE_SYSTEM_PROMPT,
    REWRITE_USER_TEMPLATE,
)
from app.prompts.web import (
    ANSWER_WITH_WEB_SYSTEM_PROMPT,
    ANSWER_WITH_WEB_USER_TEMPLATE,
    SEARCH_QUERY_SYSTEM_PROMPT,
    SEARCH_QUERY_USER_TEMPLATE,
    WEB_GRADE_SYSTEM_PROMPT,
    WEB_GRADE_USER_TEMPLATE,
    WEB_LIMIT_REACHED_NOTICE,
    WEB_NO_RESULTS_NOTICE,
    WEB_SECTION_HEADING,
    WEB_UNAVAILABLE_NOTICE,
)
from app.services.embeddings import Embedder
from app.services.llm import LLM, LLMError
from app.services.vectorstore import RetrievedChunk, VectorStore
from app.services.web_search import WebResult, WebSearcher, WebSearchStatus

logger = logging.getLogger(__name__)

# How many times retrieval is retried with a rephrased query.
MAX_RETRIEVAL_RETRIES = 1
# Web results shorter than this are treated as low quality.
_MIN_WEB_CONTENT_CHARS = 40
_MAX_SEARCH_QUERY_CHARS = 150


class ChatTurn(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class QAState(TypedDict, total=False):
    question: str
    chat_history: list[ChatTurn]
    user_id: str  # for the per-user web search limit
    document_id: str
    document_summary: str | None
    web_search_enabled: bool
    rewritten_question: str
    doc_results: list[RetrievedChunk]
    search_query: str
    web_results: list[WebResult]
    web_search_status: WebSearchStatus
    answer: str
    sources: list[dict[str, Any]]
    retries: int


class GradeResult(BaseModel):
    """Structured output of the document and web graders."""

    relevant_ids: list[int] = Field(
        default_factory=list, description="Ids of the items that help answer the question"
    )


@dataclass
class QAResult:
    answer: str
    sources: list[dict[str, Any]]
    rewritten_question: str
    web_search_status: WebSearchStatus = WebSearchStatus.NOT_REQUESTED
    doc_results: list[RetrievedChunk] = field(default_factory=list)


# ----------------------------------------------------------------- helpers
def format_history(history: list[ChatTurn]) -> str:
    return "\n".join(f"{turn['role'].capitalize()}: {turn['content']}" for turn in history)


def format_excerpts(chunks: list[RetrievedChunk]) -> str:
    """Number excerpts for the grader (ids are 1-based positions)."""
    return "\n\n".join(
        f'<excerpt id="{i}" page="{c.page_number}">\n{c.text}\n</excerpt>'
        for i, c in enumerate(chunks, start=1)
    )


def format_context(chunks: list[RetrievedChunk]) -> str:
    """Context for the answer, ordered by page so citations read naturally."""
    if not chunks:
        return "(No relevant excerpts were found in the document.)"
    ordered = sorted(chunks, key=lambda c: (c.page_number, c.chunk_index))
    return "\n\n".join(f"[Page {c.page_number}]\n{c.text}" for c in ordered)


def format_web_results(results: list[WebResult]) -> str:
    """Web results with ids [W1].. (content was sanitized by the web search service)."""
    return "\n\n".join(
        f"[W{i}] {r.title}\nURL: {r.url}\n{r.content}" for i, r in enumerate(results, start=1)
    )


_PAGE_REF = re.compile(r"\bPages?\s+(\d+(?:\s*(?:,|and|&|-|–)\s*(?:Page\s+)?\d+)*)", re.IGNORECASE)
_WEB_REF = re.compile(r"\[W(\d+)\]")


def extract_cited_pages(answer: str, allowed: set[int]) -> list[int]:
    """Pages cited as "(Page 3)", "(Pages 3, 5)" etc., limited to retrieved pages."""
    pages: set[int] = set()
    for group in _PAGE_REF.findall(answer):
        pages.update(int(n) for n in re.findall(r"\d+", group))
    return sorted(pages & allowed)


def extract_cited_web(answer: str, count: int) -> list[int]:
    """1-based web result ids cited as [W1], in first-cited order."""
    seen: list[int] = []
    for n in (int(x) for x in _WEB_REF.findall(answer)):
        if 1 <= n <= count and n not in seen:
            seen.append(n)
    return seen


def is_not_covered(answer: str) -> bool:
    return answer.strip().rstrip(".").lower() == NOT_COVERED_ANSWER.rstrip(".").lower()


def split_sections(answer: str) -> tuple[str, str]:
    """(document part, web part) of a two-section answer."""
    idx = answer.find(WEB_SECTION_HEADING)
    if idx == -1:
        return answer, ""
    return answer[:idx], answer[idx:]


_WORD = re.compile(r"[a-z0-9][a-z0-9+#.\-]{2,}")


def query_is_on_topic(query: str, *references: str | None) -> bool:
    """Guard against a model returning an unrelated query: require shared words."""
    words = set(_WORD.findall(query.lower()))
    reference = set(_WORD.findall(" ".join(r or "" for r in references).lower()))
    return bool(words & reference)


def web_notice(status: WebSearchStatus) -> str | None:
    return {
        WebSearchStatus.LIMIT_REACHED: WEB_LIMIT_REACHED_NOTICE,
        WebSearchStatus.UNAVAILABLE: WEB_UNAVAILABLE_NOTICE,
        WebSearchStatus.NO_RESULTS: WEB_NO_RESULTS_NOTICE,
    }.get(status)


def with_notice(answer: str, notice: str | None) -> str:
    return f"{answer}\n\n_{notice}_" if notice else answer


def doc_sources(pages: list[int]) -> list[dict[str, Any]]:
    return [{"type": "document", "page": p} for p in pages]


def web_sources(results: list[WebResult]) -> list[dict[str, Any]]:
    return [{"type": "web", "title": r.title, "url": r.url} for r in results]


# ------------------------------------------------------------------- graph
class QAGraph:
    """Builds and runs the compiled Q&A graph."""

    def __init__(
        self,
        llm: LLM,
        embedder: Embedder,
        vector_store: VectorStore,
        web_searcher: WebSearcher | None = None,
        *,
        top_k: int = 5,
        history_messages: int = 8,
    ) -> None:
        self._llm = llm
        self._embedder = embedder
        self._vectors = vector_store
        self._web = web_searcher
        self._top_k = top_k
        self._history_messages = history_messages
        self._graph = self._build()

    def _build(self) -> Any:
        from langgraph.graph import END, START, StateGraph

        graph = StateGraph(QAState)
        graph.add_node("rewrite_query", self.rewrite_query)
        graph.add_node("retrieve_document", self.retrieve_document)
        graph.add_node("grade_documents", self.grade_documents)
        graph.add_node("rephrase_query", self.rephrase_query)
        graph.add_node("build_search_query", self.build_search_query)
        graph.add_node("web_search", self.web_search)
        graph.add_node("grade_web_results", self.grade_web_results)
        graph.add_node("generate_answer", self.generate_answer)

        graph.add_edge(START, "rewrite_query")
        graph.add_edge("rewrite_query", "retrieve_document")
        graph.add_edge("retrieve_document", "grade_documents")
        graph.add_conditional_edges(
            "grade_documents",
            self.route_after_grading,
            {
                "rephrase": "rephrase_query",
                "web": "build_search_query",
                "answer": "generate_answer",
            },
        )
        graph.add_edge("rephrase_query", "retrieve_document")
        graph.add_edge("build_search_query", "web_search")
        graph.add_edge("web_search", "grade_web_results")
        graph.add_edge("grade_web_results", "generate_answer")
        graph.add_edge("generate_answer", END)
        return graph.compile()

    async def run(
        self,
        *,
        question: str,
        chat_history: list[ChatTurn],
        document_id: str,
        document_summary: str | None,
        web_search_enabled: bool = False,
        user_id: str = "",
    ) -> QAResult:
        state: QAState = {
            "question": question,
            "chat_history": chat_history[-self._history_messages :],
            "user_id": user_id,
            "document_id": document_id,
            "document_summary": document_summary,
            "web_search_enabled": web_search_enabled,
            "web_search_status": WebSearchStatus.NOT_REQUESTED,
            "retries": 0,
            "doc_results": [],
            "web_results": [],
            "sources": [],
        }
        final: QAState = await self._graph.ainvoke(state)
        return QAResult(
            answer=final["answer"],
            sources=final.get("sources", []),
            rewritten_question=final.get("rewritten_question", question),
            web_search_status=final.get("web_search_status", WebSearchStatus.NOT_REQUESTED),
            doc_results=final.get("doc_results", []),
        )

    # ------------------------------------------------------ document nodes
    async def rewrite_query(self, state: QAState) -> QAState:
        """Turn a follow-up into a standalone question using recent history."""
        history = state.get("chat_history") or []
        if not history:
            # First question in a session is already standalone: save an LLM call.
            return {"rewritten_question": state["question"]}
        try:
            rewritten = await self._llm.generate_text(
                [
                    SystemMessage(REWRITE_SYSTEM_PROMPT),
                    HumanMessage(
                        REWRITE_USER_TEMPLATE.format(
                            history=format_history(history), question=state["question"]
                        )
                    ),
                ]
            )
        except LLMError:
            logger.warning("query rewrite failed; using original question")
            rewritten = ""
        return {"rewritten_question": rewritten.strip() or state["question"]}

    async def retrieve_document(self, state: QAState) -> QAState:
        """Top-k similarity search in the document's own namespace."""
        vector = await self._embedder.embed_query(state["rewritten_question"])
        results = await self._vectors.query(state["document_id"], vector, self._top_k)
        logger.info(
            "retrieved chunks", extra={"count": len(results), "retries": state.get("retries", 0)}
        )
        return {"doc_results": results}

    async def grade_documents(self, state: QAState) -> QAState:
        """Keep only chunks the LLM judges relevant (one call for all chunks)."""
        chunks = state.get("doc_results") or []
        if not chunks:
            return {"doc_results": []}
        try:
            grade = await self._llm.generate_structured(
                [
                    SystemMessage(GRADE_SYSTEM_PROMPT),
                    HumanMessage(
                        GRADE_USER_TEMPLATE.format(
                            question=state["rewritten_question"], excerpts=format_excerpts(chunks)
                        )
                    ),
                ],
                GradeResult,
            )
        except LLMError:
            # Fail open: the answer prompt still refuses when context is irrelevant.
            logger.warning("document grading failed; keeping all retrieved chunks")
            return {"doc_results": chunks}
        keep = {i for i in grade.relevant_ids if 1 <= i <= len(chunks)}
        relevant = [c for i, c in enumerate(chunks, start=1) if i in keep]
        logger.info("graded chunks", extra={"retrieved": len(chunks), "relevant": len(relevant)})
        return {"doc_results": relevant}

    def route_after_grading(self, state: QAState) -> str:
        if not state.get("doc_results") and state.get("retries", 0) < MAX_RETRIEVAL_RETRIES:
            return "rephrase"
        return "web" if state.get("web_search_enabled") else "answer"

    async def rephrase_query(self, state: QAState) -> QAState:
        """Reword the question for one more retrieval attempt."""
        retries = state.get("retries", 0) + 1
        try:
            rephrased = await self._llm.generate_text(
                [
                    SystemMessage(REPHRASE_SYSTEM_PROMPT),
                    HumanMessage(
                        REPHRASE_USER_TEMPLATE.format(
                            summary=state.get("document_summary") or "(not available)",
                            question=state["rewritten_question"],
                        )
                    ),
                ]
            )
        except LLMError:
            rephrased = ""
        return {
            "rewritten_question": rephrased.strip() or state["rewritten_question"],
            "retries": retries,
        }

    # ----------------------------------------------------------- web nodes
    async def build_search_query(self, state: QAState) -> QAState:
        """Short focused query from the standalone question + document summary."""
        question = state["rewritten_question"]
        try:
            query = await self._llm.generate_text(
                [
                    SystemMessage(SEARCH_QUERY_SYSTEM_PROMPT),
                    HumanMessage(
                        SEARCH_QUERY_USER_TEMPLATE.format(
                            summary=state.get("document_summary") or "(not available)",
                            question=question,
                        )
                    ),
                ]
            )
        except LLMError:
            query = ""
        query = query.strip().strip("\"'").splitlines()[0].strip() if query.strip() else ""
        if not query_is_on_topic(query, question, state.get("document_summary")):
            logger.warning("generated search query off-topic; using the question instead")
            query = question
        logger.debug("web search query", extra={"search_query": query})
        return {"search_query": query[:_MAX_SEARCH_QUERY_CHARS]}

    async def web_search(self, state: QAState) -> QAState:
        """DuckDuckGo search (limits, cache and fallbacks live in the service)."""
        if self._web is None:
            return {"web_results": [], "web_search_status": WebSearchStatus.UNAVAILABLE}
        outcome = await self._web.search(state.get("user_id", ""), state["search_query"])
        return {"web_results": outcome.results, "web_search_status": outcome.status}

    async def grade_web_results(self, state: QAState) -> QAState:
        """Drop low-quality results (heuristics), then irrelevant ones (LLM)."""
        if state.get("web_search_status") != WebSearchStatus.USED:
            return {"web_results": []}
        results = [
            r for r in state.get("web_results") or [] if len(r.content) >= _MIN_WEB_CONTENT_CHARS
        ]
        if not results:
            return {"web_results": [], "web_search_status": WebSearchStatus.NO_RESULTS}
        try:
            grade = await self._llm.generate_structured(
                [
                    SystemMessage(WEB_GRADE_SYSTEM_PROMPT),
                    HumanMessage(
                        WEB_GRADE_USER_TEMPLATE.format(
                            question=state["rewritten_question"],
                            results=format_web_results(results),
                        )
                    ),
                ],
                GradeResult,
            )
            keep = {i for i in grade.relevant_ids if 1 <= i <= len(results)}
            results = [r for i, r in enumerate(results, start=1) if i in keep]
        except LLMError:
            logger.warning("web grading failed; keeping heuristic-filtered results")
        logger.info("graded web results", extra={"kept": len(results)})
        if not results:
            return {"web_results": [], "web_search_status": WebSearchStatus.NO_RESULTS}
        return {"web_results": results}

    # -------------------------------------------------------------- answer
    async def generate_answer(self, state: QAState) -> QAState:
        chunks = state.get("doc_results") or []
        web = state.get("web_results") or []
        status = state.get("web_search_status", WebSearchStatus.NOT_REQUESTED)
        notice = web_notice(status) if state.get("web_search_enabled") else None

        if web:
            return await self._answer_with_web(state, chunks, web)
        if not chunks:
            # Nothing relevant anywhere: answer deterministically, never guess.
            return {"answer": with_notice(NOT_COVERED_ANSWER, notice), "sources": []}

        answer = await self._llm.generate_text(
            [
                SystemMessage(ANSWER_SYSTEM_PROMPT),
                HumanMessage(
                    ANSWER_USER_TEMPLATE.format(
                        context=format_context(chunks), question=state["rewritten_question"]
                    )
                ),
            ]
        )
        answer = answer.strip() or NOT_COVERED_ANSWER
        if is_not_covered(answer):
            return {"answer": with_notice(NOT_COVERED_ANSWER, notice), "sources": []}
        context_pages = {c.page_number for c in chunks}
        pages = extract_cited_pages(answer, context_pages) or sorted(context_pages)
        return {"answer": with_notice(answer, notice), "sources": doc_sources(pages)}

    async def _answer_with_web(
        self, state: QAState, chunks: list[RetrievedChunk], web: list[WebResult]
    ) -> QAState:
        """Two-section answer: document first, then clearly labeled web context."""
        answer = await self._llm.generate_text(
            [
                SystemMessage(ANSWER_WITH_WEB_SYSTEM_PROMPT),
                HumanMessage(
                    ANSWER_WITH_WEB_USER_TEMPLATE.format(
                        context=format_context(chunks),
                        web=format_web_results(web),
                        question=state["rewritten_question"],
                    )
                ),
            ]
        )
        answer = answer.strip()
        doc_part, web_part = split_sections(answer)

        context_pages = {c.page_number for c in chunks}
        pages = extract_cited_pages(doc_part, context_pages)
        if not pages and chunks and NOT_COVERED_ANSWER.rstrip(".").lower() not in doc_part.lower():
            pages = sorted(context_pages)
        # Only web results the answer actually cites become sources.
        cited_web = [web[i - 1] for i in extract_cited_web(web_part or answer, len(web))]
        return {"answer": answer, "sources": doc_sources(pages) + web_sources(cited_web)}
