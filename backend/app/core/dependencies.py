"""Shared FastAPI dependencies.

`get_current_user` is stubbed to a single fixed local user: real Google-login
auth (JWT issuance/verification) was deferred so development could focus on
the RAG pipeline first. Every protected endpoint still depends on this
function and every repository call still takes a `user_id` and checks
ownership, so swapping in real auth later is a one-function change here, not
a rewrite of the API/repository layers.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request

from app.config import Settings
from app.db.mongo import get_db
from app.db.repositories.chat import ChatMessageRepository, ChatSessionRepository
from app.db.repositories.documents import DocumentRepository
from app.db.repositories.quiz import QuizAttemptRepository, QuizRepository
from app.graphs.qa_graph import QAGraph
from app.graphs.quiz_graph import QuizGraph
from app.schemas.user import CurrentUser
from app.services.chat import ChatService
from app.services.container import Services
from app.services.documents import DocumentService
from app.services.ingestion import IngestionService
from app.services.quiz import QuizService
from app.services.web_search import WebSearchService

# Fixed identity used while Google auth (Phase 2) is deferred.
LOCAL_DEV_USER = CurrentUser(
    user_id="local-dev-user",
    email="dev@example.com",
    name="Local Dev User",
)


async def get_current_user() -> CurrentUser:
    """Return the current request's user.

    TODO(auth): replace this with real JWT verification once Google login is
    implemented. Keeping the dependency in place now means every endpoint
    already written against `Depends(get_current_user)` needs no changes then.
    """
    return LOCAL_DEV_USER


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


def get_document_repo(db: Annotated[Any, Depends(get_db)]) -> DocumentRepository:
    return DocumentRepository(db)


def get_document_service(
    settings: Annotated[Settings, Depends(get_app_settings)],
    repo: Annotated[DocumentRepository, Depends(get_document_repo)],
    services: Annotated[Services, Depends(get_services)],
) -> DocumentService:
    return DocumentService(settings, repo, services.vector_store)


def get_ingestion_service(
    settings: Annotated[Settings, Depends(get_app_settings)],
    repo: Annotated[DocumentRepository, Depends(get_document_repo)],
    services: Annotated[Services, Depends(get_services)],
) -> IngestionService:
    return IngestionService(settings, repo, services.embedder, services.vector_store, services.llm)


def get_qa_graph(request: Request) -> QAGraph:
    graph: QAGraph = request.app.state.qa_graph
    return graph


def get_chat_service(
    settings: Annotated[Settings, Depends(get_app_settings)],
    db: Annotated[Any, Depends(get_db)],
    qa_graph: Annotated[QAGraph, Depends(get_qa_graph)],
) -> ChatService:
    return ChatService(
        ChatSessionRepository(db),
        ChatMessageRepository(db),
        DocumentRepository(db),
        qa_graph,
        history_messages=settings.chat_history_messages,
    )


def get_web_search_service(request: Request) -> WebSearchService:
    service: WebSearchService = request.app.state.web_search
    return service


def get_quiz_graph(request: Request) -> QuizGraph:
    graph: QuizGraph = request.app.state.quiz_graph
    return graph


def get_quiz_service(
    settings: Annotated[Settings, Depends(get_app_settings)],
    db: Annotated[Any, Depends(get_db)],
    graph: Annotated[QuizGraph, Depends(get_quiz_graph)],
    services: Annotated[Services, Depends(get_services)],
) -> QuizService:
    return QuizService(
        settings,
        QuizRepository(db),
        QuizAttemptRepository(db),
        DocumentRepository(db),
        graph,
        services.llm,
    )
