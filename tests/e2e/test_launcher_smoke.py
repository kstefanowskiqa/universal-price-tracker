from __future__ import annotations

import socket
from pathlib import Path

import httpx
import pytest

from price_tracker.config.settings import Settings
from price_tracker.desktop.launcher import LocalBackendManager


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.e2e
def test_local_backend_launcher_migrates_starts_and_stops_cleanly(tmp_path: Path) -> None:
    port = _find_free_port()
    token = "launcher-smoke-token"
    settings = Settings(
        environment="test",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        config_dir=tmp_path / "config",
        api_host="127.0.0.1",
        api_port=port,
        api_token=token,
    )
    manager = LocalBackendManager(settings=settings)

    try:
        started = manager.ensure_started(startup_timeout_seconds=20.0)
        assert started is True

        # /health bez tokenu -> 200 OK
        health = httpx.get(f"{manager.base_url}/health", timeout=3.0)
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        # Chroniony endpoint bez tokenu -> 401
        unauth = httpx.get(f"{manager.base_url}/api/v1/system/info", timeout=3.0)
        assert unauth.status_code == 401

        # Chroniony endpoint z tokenem -> 200 i właściwy dialekt
        info = httpx.get(
            f"{manager.base_url}/api/v1/system/info",
            headers={"X-API-Token": token},
            timeout=3.0,
        )
        assert info.status_code == 200
        assert info.json()["database_dialect"] == "sqlite"

        # Baza utworzona wyłącznie w izolowanym katalogu testowym
        assert (tmp_path / "data" / "price_tracker.db").exists()
    finally:
        manager.stop()

    assert manager._process is None
