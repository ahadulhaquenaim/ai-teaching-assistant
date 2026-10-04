"""Health endpoint used by Render health checks and the frontend wake-up probe."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app import __version__
from app.db.mongo import MongoManager, get_mongo
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "A dependency is unavailable"}},
)
async def health(
    response: Response, mongo: Annotated[MongoManager, Depends(get_mongo)]
) -> HealthResponse:
    """Report app liveness and MongoDB connectivity."""
    mongo_ok = await mongo.ping()
    if not mongo_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if mongo_ok else "degraded",
        mongo="ok" if mongo_ok else "unavailable",
        version=__version__,
    )
