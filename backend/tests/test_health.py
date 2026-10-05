"""Tests for GET /health and app startup."""

from __future__ import annotations

import httpx
from app.db.indexes import INDEXES

from tests.conftest import FakeMongo


async def test_health_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["mongo"] == "ok"
    assert "version" in body
    assert response.headers["X-Request-ID"]


async def test_health_degraded_when_mongo_down(
    client: httpx.AsyncClient, fake_mongo: FakeMongo
) -> None:
    fake_mongo.healthy = False

    response = await client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "mongo": "unavailable", "version": "0.1.0"}


async def test_request_id_is_propagated(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "abc123"})

    assert response.headers["X-Request-ID"] == "abc123"


async def test_startup_creates_all_indexes(
    client: httpx.AsyncClient, fake_mongo: FakeMongo
) -> None:
    created = fake_mongo.db.index_calls
    assert len(created) == len(INDEXES)
    assert ("users", [("google_id", 1)], {"unique": True}) in created
    unique_usage = ("web_search_usage", [("user_id", 1), ("date", 1)], {"unique": True})
    assert unique_usage in created
    ttl = [opts for coll, _, opts in created if coll == "web_search_cache" and opts]
    assert {"expireAfterSeconds": 86400} in ttl


async def test_unknown_route_uses_error_envelope(client: httpx.AsyncClient) -> None:
    response = await client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_error"
