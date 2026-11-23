from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from price_tracker.config.secrets import SecretStore
from price_tracker.desktop.client import ApiClient, ApiClientError
from price_tracker.desktop.config import DesktopConfig, DesktopConfigStore
from price_tracker.desktop.theme import build_stylesheet, get_palette
from price_tracker.desktop.widgets import compute_chart_bounds


class _MemorySecrets(SecretStore):
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get_secret(self, key: str) -> str | None:
        return self._store.get(key)

    def set_secret(self, key: str, value: str) -> None:
        self._store[key] = value

    def delete_secret(self, key: str) -> None:
        self._store.pop(key, None)


def test_desktop_config_persistence_and_remote_token(tmp_path: Path) -> None:
    secrets = _MemorySecrets()
    store = DesktopConfigStore(
        config_path=tmp_path / "client.json",
        secret_store=secrets,
    )
    cfg = store.load()
    assert cfg.connection_mode == "local"
    assert cfg.theme == "dark"

    updated = DesktopConfig(
        connection_mode="remote",
        remote_url="https://tracker.example.com",
        theme="light",
        window_width=1440,
        window_height=900,
    )
    store.save(updated)
    store.set_remote_token("secret-vps-token")

    reloaded = store.load()
    assert reloaded.connection_mode == "remote"
    assert reloaded.remote_url == "https://tracker.example.com"
    assert reloaded.theme == "light"
    assert store.get_remote_token() == "secret-vps-token"


def test_api_client_sends_token_and_raises_structured_errors() -> None:
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(dict(request.headers))
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "database": "ok"})
        if request.url.path == "/api/v1/products/999":
            return httpx.Response(404, json={"detail": "Produkt nie istnieje"})
        return httpx.Response(200, json=[])

    client = ApiClient(
        "http://127.0.0.1:8765",
        "my-local-token",
        transport=httpx.MockTransport(handler),
    )
    assert client.health()["status"] == "ok"
    assert client.list_products() == []
    assert seen_headers.get("x-api-token") == "my-local-token"

    with pytest.raises(ApiClientError, match="Produkt nie istnieje") as exc_info:
        client.delete_product(999)
    assert exc_info.value.status_code == 404


def test_themes_generate_valid_qss_without_gradients() -> None:
    for name in ("dark", "light"):
        qss = build_stylesheet(get_palette(name))  # type: ignore[arg-type]
        assert "QMainWindow" in qss
        assert "QTableWidget" in qss
        assert "qlineargradient" not in qss.lower()

def test_chart_bounds_scale_with_selected_time_range() -> None:
    now_ts = 1_800_000_000.0
    single_x = [now_ts - 300.0]
    single_y = [460.0]

    x_24h, y_24h = compute_chart_bounds(single_x, single_y, "24h", now_ts=now_ts)
    x_7d, _ = compute_chart_bounds(single_x, single_y, "7d", now_ts=now_ts)
    x_180d, _ = compute_chart_bounds(single_x, single_y, "180d", now_ts=now_ts)
    x_all, _ = compute_chart_bounds(single_x, single_y, "all", now_ts=now_ts)

    assert (x_24h[1] - x_24h[0]) < (x_7d[1] - x_7d[0]) < (x_180d[1] - x_180d[0])
    # Dla pojedynczego punktu w 'all' zakres to 2h, a nie kilka sekund
    assert (x_all[1] - x_all[0]) >= 7200.0
    assert y_24h is not None
    assert y_24h[0] < 460.0 < y_24h[1]
