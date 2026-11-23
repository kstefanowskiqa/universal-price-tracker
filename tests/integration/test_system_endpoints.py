from __future__ import annotations

import json

import httpx

from price_tracker.config.settings import Settings


async def test_app_settings_get_and_update(
    api_client: httpx.AsyncClient,
    test_settings: Settings,
) -> None:
    headers = {"X-API-Token": test_settings.resolve_api_token()}

    initial = await api_client.get("/api/v1/system/settings", headers=headers)
    assert initial.status_code == 200
    assert initial.json()["default_check_interval_seconds"] == 3600
    assert initial.json()["theme"] == "dark"

    updated = await api_client.put(
        "/api/v1/system/settings",
        headers=headers,
        json={"default_check_interval_seconds": 900, "theme": "light"},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["default_check_interval_seconds"] == 900
    assert body["theme"] == "light"

    persisted = await api_client.get("/api/v1/system/settings", headers=headers)
    assert persisted.json()["default_check_interval_seconds"] == 900


async def test_system_logs_endpoint_reads_and_filters_jsonl(
    api_client: httpx.AsyncClient,
    test_settings: Settings,
) -> None:
    headers = {"X-API-Token": test_settings.resolve_api_token()}
    log_file = test_settings.resolved_log_dir / "price_tracker.jsonl"
    entries = [
        {"timestamp": "2026-11-22T18:00:00Z", "level": "info", "event": "backend_starting"},
        {
            "timestamp": "2026-11-22T18:01:00Z",
            "level": "warning",
            "event": "price_check_failed",
            "domain": "x-kom.pl",
        },
        {
            "timestamp": "2026-11-22T18:02:00Z",
            "level": "error",
            "event": "db_timeout",
            "domain": "hm.com",
        },
    ]
    log_file.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")

    all_logs = (await api_client.get("/api/v1/system/logs", headers=headers)).json()
    assert len(all_logs) == 3
    # Najnowszy wpis pierwszy
    assert all_logs[0]["event"] == "db_timeout"

    warn_logs = (
        await api_client.get(
            "/api/v1/system/logs",
            headers=headers,
            params={"level": "warning", "search": "x-kom"},
        )
    ).json()
    assert len(warn_logs) == 1
    assert warn_logs[0]["event"] == "price_check_failed"
