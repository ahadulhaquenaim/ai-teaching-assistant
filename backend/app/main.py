"""FastAPI application factory and lifespan.

Run locally from the `backend/` directory:

    uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pymongo.errors import PyMongoError

from app import __version__
from app.api import chat, documents, health, quiz, usage
from app.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import RequestLoggingMiddleware, configure_logging
from app.db.indexes import ensure_indexes
from app.db.mongo import MongoManager
from app.db.repositories.documents import DocumentRepository
from app.db.repositories.web_search import WebSearchCacheRepository, WebSearchUsageRepository
from app.graphs.qa_graph import QAGraph
from app.graphs.quiz_graph import QuizGraph
from app.services.container import Services, build_services
from app.services.web_search import WebSearchService

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    mongo: MongoManager | None = None,
    services: Services | None = None,
) -> FastAPI:
    """Build the FastAPI app. Arguments allow tests to inject fakes."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)
    mongo = mongo or MongoManager(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await mongo.connect()
        app.state.mongo = mongo
        app.state.services = svc = services or build_services(settings)
        app.state.web_search = WebSearchService(
            svc.web_provider,
            WebSearchUsageRepository(mongo.db),
            WebSearchCacheRepository(mongo.db),
            settings,
        )
        # Compile the graph once; it holds no per-request state.
        app.state.qa_graph = QAGraph(
            svc.llm,
            svc.embedder,
            svc.vector_store,
            app.state.web_search,
            top_k=settings.retrieval_top_k,
            history_messages=settings.chat_history_messages,
        )
        app.state.quiz_graph = QuizGraph(
            svc.llm,
            svc.embedder,
            svc.vector_store,
            context_chunks=settings.quiz_context_chunks,
            context_max_chars=settings.quiz_context_max_chars,
            max_retries=settings.quiz_max_retries,
        )
        try:
            await ensure_indexes(mongo.db)
            # Background ingestion does not survive a restart; fail orphans.
            await DocumentRepository(mongo.db).fail_interrupted()
        except PyMongoError:
            # Keep serving so /health can report the problem instead of the
            # instance crash-looping on Render.
            logger.exception("mongo startup tasks failed")
        logger.info("startup complete", extra={"env": settings.environment})
        yield
        await app.state.services.aclose()
        await mongo.close()
        logger.info("shutdown complete")

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        # Hide interactive docs in production.
        docs_url=None if settings.environment == "production" else "/docs",
        redoc_url=None,
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )
    app.add_middleware(RequestLoggingMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(documents.router)
    app.include_router(chat.router)
    app.include_router(quiz.router)
    app.include_router(usage.router)
    return app


app = create_app()
