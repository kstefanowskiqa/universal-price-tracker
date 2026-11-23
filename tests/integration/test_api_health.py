from __future__ import annotations

import httpx

from price_tracker.config.settings import Settings


async def test_health_endpoint_returns_ok(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert "timestamp_utc" in body


async def test_system_info_requires_valid_api_token(
    api_client: httpx.AsyncClient, test_settings: Settings
) -> None:
    unauthenticated = await api_client.get("/api/v1/system/info")
    assert unauthenticated.status_code == 401

    wrong_token = await api_client.get(
        "/api/v1/system/info", headers={"X-API-Token": "wrong-token"}
    )
    assert wrong_token.status_code == 401

    authorized = await api_client.get(
        "/api/v1/system/info",
        headers={"X-API-Token": test_settings.resolve_api_token()},
    )
    assert authorized.status_code == 200
    data = authorized.json()
    assert data["database_dialect"] == "sqlite"
    assert data["environment"] == "test"
