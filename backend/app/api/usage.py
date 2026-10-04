"""Usage endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.dependencies import CurrentUserDep, get_web_search_service
from app.db.repositories.web_search import today_utc
from app.schemas.usage import WebSearchUsageOut
from app.services.web_search import WebSearchService

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/web-search", response_model=WebSearchUsageOut)
async def web_search_usage(
    user: CurrentUserDep, service: Annotated[WebSearchService, Depends(get_web_search_service)]
) -> WebSearchUsageOut:
    """Today's web searches (UTC day): limit, used, and remaining."""
    used, remaining = await service.remaining(user.user_id)
    return WebSearchUsageOut(date=today_utc(), limit=service.daily_limit, used=used, remaining=remaining)
