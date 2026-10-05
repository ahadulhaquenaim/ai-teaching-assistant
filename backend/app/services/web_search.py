"""Web search: DuckDuckGo (ddgs) behind a daily per-user limit and a 24h cache.

Web content is UNTRUSTED. It is sanitized and truncated here, and the graph
places it in a delimited section that the LLM is told to treat as reference
data only, never as instructions.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Protocol

from app.config import Settings
from app.db.repositories.web_search import WebSearchCacheRepository, WebSearchUsageRepository
from app.services.retry import with_retry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    content: str


class WebSearchStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    USED = "used"
    NO_RESULTS = "no_results"
    LIMIT_REACHED = "limit_reached"
    UNAVAILABLE = "unavailable"


@dataclass
class WebSearchOutcome:
    status: WebSearchStatus
    results: list[WebResult] = field(default_factory=list)


# ------------------------------------------------------------------ sanitize
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_web_text(text: str, max_chars: int) -> str:
    """Strip control chars and angle brackets, collapse whitespace, truncate.

    Removing `<`/`>` means a page cannot close our delimiter tags or forge new
    ones (e.g. `</untrusted_web_content>`) to escape the untrusted section.
    """
    text = _CONTROL_CHARS.sub(" ", text or "")
    text = text.replace("<", "‹").replace(">", "›")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + " …"
    return text


def query_hash(query: str, backend: str, region: str) -> str:
    normalized = re.sub(r"\s+", " ", query.strip().lower())
    return hashlib.sha256(f"{backend}|{region}|{normalized}".encode()).hexdigest()


# ------------------------------------------------------------------ provider
class SearchProvider(Protocol):
    async def search(self, query: str, max_results: int) -> list[WebResult]: ...


class DuckDuckGoProvider:
    """DuckDuckGo via `ddgs`. The client is synchronous, so it runs in a thread."""

    def __init__(self, settings: Settings) -> None:
        self._timeout = settings.web_search_timeout_seconds
        self._backend = settings.web_search_backend
        self._region = settings.web_search_region
        self._max_chars = settings.web_search_max_chars_per_result

    def _search_sync(self, query: str, max_results: int) -> list[dict[str, str]]:
        from ddgs import DDGS
        from ddgs.exceptions import DDGSException

        try:
            return DDGS(timeout=int(self._timeout)).text(
                query,
                max_results=max_results,
                backend=self._backend,
                region=self._region,
                safesearch="moderate",
            )
        except DDGSException as exc:
            # ddgs signals an empty result set with an exception, not [].
            if "no results" in str(exc).lower():
                return []
            raise

    async def search(self, query: str, max_results: int) -> list[WebResult]:
        async def once() -> list[dict[str, str]]:
            return await asyncio.wait_for(
                asyncio.to_thread(self._search_sync, query, max_results), self._timeout + 5
            )

        raw = await with_retry(
            once, attempts=2, initial_delay=1.0, max_delay=3.0, retry_if=_ddgs_retryable
        )
        results: list[WebResult] = []
        seen: set[str] = set()
        for item in raw:
            url = str(item.get("href") or "").strip()
            if not url.startswith(("https://", "http://")) or url in seen:
                continue
            seen.add(url)
            results.append(
                WebResult(
                    title=sanitize_web_text(str(item.get("title") or url), 200),
                    url=url,
                    content=sanitize_web_text(str(item.get("body") or ""), self._max_chars),
                )
            )
        return results[:max_results]


def _ddgs_retryable(exc: BaseException) -> bool:
    return type(exc).__name__ in {"RatelimitException", "TimeoutException", "TimeoutError"}


# ------------------------------------------------------------------- service
class WebSearcher(Protocol):
    """Interface used by the Q&A graph."""

    async def search(self, user_id: str, query: str) -> WebSearchOutcome: ...


class WebSearchService:
    def __init__(
        self,
        provider: SearchProvider,
        usage: WebSearchUsageRepository,
        cache: WebSearchCacheRepository,
        settings: Settings,
    ) -> None:
        self._provider = provider
        self._usage = usage
        self._cache = cache
        self._settings = settings

    @property
    def daily_limit(self) -> int:
        return self._settings.web_search_daily_limit

    async def remaining(self, user_id: str) -> tuple[int, int]:
        """(used_today, remaining_today)"""
        used = await self._usage.used_today(user_id)
        return used, max(0, self.daily_limit - used)

    async def search(self, user_id: str, query: str) -> WebSearchOutcome:
        """Cache -> daily limit -> DuckDuckGo. Never raises."""
        s = self._settings
        key = query_hash(query, s.web_search_backend, s.web_search_region)

        if s.web_search_cache_enabled:
            try:
                cached = await self._cache.get(key)
            except Exception:
                logger.warning("web search cache read failed", exc_info=True)
                cached = None
            if cached is not None:
                logger.info("web search cache hit", extra={"results": len(cached)})
                results = [WebResult(**r) for r in cached]
                return WebSearchOutcome(
                    WebSearchStatus.USED if results else WebSearchStatus.NO_RESULTS, results
                )

        if not await self._usage.try_consume(user_id, self.daily_limit):
            logger.info("web search daily limit reached", extra={"limit": self.daily_limit})
            return WebSearchOutcome(WebSearchStatus.LIMIT_REACHED)

        try:
            results = await self._provider.search(query, s.web_search_max_results)
        except Exception as exc:
            logger.warning("web search failed", extra={"error_type": type(exc).__name__})
            await self._usage.refund(user_id)
            return WebSearchOutcome(WebSearchStatus.UNAVAILABLE)

        if s.web_search_cache_enabled and results:
            try:
                await self._cache.set(key, [asdict(r) for r in results])
            except Exception:
                logger.warning("web search cache write failed", exc_info=True)
        logger.info("web search done", extra={"results": len(results)})
        return WebSearchOutcome(
            WebSearchStatus.USED if results else WebSearchStatus.NO_RESULTS, results
        )
