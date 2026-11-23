from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import Any

import httpx
import pytest
import uvicorn
from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import QMessageBox

from price_tracker.api.app import create_app
from price_tracker.config.secrets import SecretStore
from price_tracker.config.settings import Settings
from price_tracker.db.models import Base
from price_tracker.db.session import DatabaseManager
from price_tracker.desktop.client import ApiClient
from price_tracker.desktop.config import DesktopConfig, DesktopConfigStore
from price_tracker.desktop.main_window import MainWindow
from price_tracker.desktop.widgets import AddProductDialog
from price_tracker.notifications.channels import build_default_channel_registry
from price_tracker.notifications.service import NotificationService
from price_tracker.scheduler.domain_limiter import DomainRateLimiter
from price_tracker.scheduler.worker import MonitoringScheduler
from price_tracker.scraping.engine import ScrapingEngine
from price_tracker.scraping.http_client import SafeHttpClient


class _MemorySecrets(SecretStore):
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get_secret(self, key: str) -> str | None:
        return self._data.get(key)

    def set_secret(self, key: str, value: str) -> None:
        self._data[key] = value

    def delete_secret(self, key: str) -> None:
        self._data.pop(key, None)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _product_html(name: str, price: str) -> str:
    return f"""
    <html><head>
    <script type="application/ld+json">
    {{
      "@context": "https://schema.org",
      "@type": "Product",
      "name": "{name}",
      "offers": {{
        "@type": "Offer",
        "price": "{price}",
        "priceCurrency": "PLN",
        "availability": "https://schema.org/InStock"
      }}
    }}
    </script>
    </head><body><h1>{name}</h1></body></html>
    """


@pytest.fixture()
def fail_on_qt_exception() -> Iterator[None]:
    """Zamienia każdy cichy wyjątek rzucony w slocie PySide6 w twardy błąd testu."""
    captured: list[BaseException] = []
    original_hook = sys.excepthook

    def _hook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: TracebackType | None,
    ) -> None:
        captured.append(exc_value)
        original_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
    try:
        yield
    finally:
        sys.excepthook = original_hook
        assert not captured, f"Wykryto nieobsłużone wyjątki w GUI Qt: {captured}"


@pytest.fixture()
def live_e2e_backend(tmp_path: Path) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """Uruchamia prawdziwy backend FastAPI + SQLite + harmonogram na wolnym porcie."""
    port = _find_free_port()
    token = "e2e-gui-test-token"
    base_url = f"http://127.0.0.1:{port}"
    state: dict[str, Any] = {
        "xkom_price": "2500.00",
        "other_price": "2399.00",
        "telegram_messages": [],
    }

    settings = Settings(
        environment="test",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        config_dir=tmp_path / "config",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'e2e.db').as_posix()}",
        api_host="127.0.0.1",
        api_port=port,
        api_token=token,
    )

    server_holder: dict[str, uvicorn.Server] = {}
    startup_error: list[BaseException] = []

    async def _public_dns(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34"]

    def _shop_handler(request: httpx.Request) -> httpx.Response:
        if "x-kom.pl" in request.url.host:
            return httpx.Response(200, text=_product_html("Laptop E2E Pro", state["xkom_price"]))
        return httpx.Response(200, text=_product_html("Laptop E2E Pro", state["other_price"]))

    def _telegram_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        state["telegram_messages"].append(str(body["text"]))
        msg_id = len(state["telegram_messages"])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": msg_id}})

    def _run_server() -> None:
        async def _serve() -> None:
            db = DatabaseManager(settings.resolved_database_url)
            async with db.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            secrets = _MemorySecrets()
            http_client = SafeHttpClient(
                resolver=_public_dns,
                transport=httpx.MockTransport(_shop_handler),
            )
            engine = ScrapingEngine(http_client=http_client, enable_browser_fallback=False)
            registry = build_default_channel_registry(
                telegram_transport=httpx.MockTransport(_telegram_handler)
            )
            notif_service = NotificationService(
                db,
                secret_store=secrets,
                channel_registry=registry,
            )
            limiter = DomainRateLimiter(default_domain_delay_seconds=0.0, jitter_ratio=0.0)
            scheduler = MonitoringScheduler(
                db,
                engine,
                rate_limiter=limiter,
                default_interval_seconds=3600,
                on_check_completed=notif_service.handle_price_check_event,
            )
            app = create_app(
                settings=settings,
                db_manager=db,
                scraping_engine=engine,
                scheduler=scheduler,
                notification_service=notif_service,
                secret_store=secrets,
                start_scheduler=True,
            )

            config = uvicorn.Config(
                app=app,
                host="127.0.0.1",
                port=port,
                log_level="warning",
            )
            server = uvicorn.Server(config)
            server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
            server_holder["server"] = server
            try:
                await server.serve()
            finally:
                await notif_service.close()
                await engine.close()
                await db.dispose()

        try:
            asyncio.run(_serve())
        except BaseException as exc:
            startup_error.append(exc)

    thread = threading.Thread(target=_run_server, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10.0
    healthy = False
    while time.monotonic() < deadline:
        if startup_error:
            raise RuntimeError(f"Backend E2E nie wystartował: {startup_error[0]}")
        try:
            resp = httpx.get(f"{base_url}/health", timeout=0.5)
            if resp.status_code == 200:
                healthy = True
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)

    assert healthy, "Backend E2E nie odpowiedział na /health w zadanym czasie."

    try:
        yield base_url, token, state
    finally:
        srv = server_holder.get("server")
        if srv is not None:
            srv.should_exit = True
        thread.join(timeout=10.0)


@pytest.mark.e2e
def test_full_desktop_user_journey(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    live_e2e_backend: tuple[str, str, dict[str, Any]],
    fail_on_qt_exception: None,
) -> None:
    """Pełny test E2E: GUI PySide6 + żywy serwer API + harmonogram + wykresy + Telegram."""
    base_url, token, state = live_e2e_backend

    # Zabezpieczenie przed blokowaniem testu przez modalne okna QMessageBox
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *_a, **_kw: QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_a, **_kw: QMessageBox.StandardButton.Yes,
    )

    api = ApiClient(base_url=base_url, api_token=token)
    config_store = DesktopConfigStore(
        config_path=tmp_path / "desktop_client.json",
        secret_store=_MemorySecrets(),
    )
    desktop_cfg = DesktopConfig(auto_refresh_seconds=60, theme="dark")
    window = MainWindow(api=api, config_store=config_store, desktop_config=desktop_cfg)
    qtbot.addWidget(window)
    window.show()

    try:
        # 1. Dashboard ładuje się z żywego API
        qtbot.waitUntil(
            lambda: "Produkty: 0" in window._dashboard_view.metrics_strip.text(),
            timeout=5000,
        )

        # 2. Dialog dodawania produktu: podgląd z URL i zapis
        dlg = AddProductDialog(api, categories=[], parent=window)
        qtbot.addWidget(dlg)
        dlg.url_edit.setText("https://www.x-kom.pl/p/laptop-e2e.html")
        qtbot.mouseClick(dlg.preview_btn, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: dlg._preview_ok, timeout=6000)
        assert dlg.name_edit.text() == "Laptop E2E Pro"
        assert dlg.price_edit.text() == "2500.00"

        assert dlg._ok_btn is not None
        qtbot.mouseClick(dlg._ok_btn, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: dlg.created_product is not None, timeout=6000)
        qtbot.waitUntil(lambda: dlg.created_product is not None, timeout=6000)
        assert dlg.created_product is not None
        product_id = int(dlg.created_product["id"])
        offer_1_id = int(dlg.created_product["offers"][0]["id"])

        # Dodanie drugiego sklepu do tego samego produktu (porównywarka)
        offer_2 = api.add_offer(
            product_id,
            {"url": "https://audio-shop.pl/laptop-e2e", "variant_label": "16GB"},
        )
        offer_2_id = int(offer_2["id"])

        # 3. Konfiguracja Telegrama w zakładce Ustawienia + wiadomość testowa
        qtbot.mouseClick(window._nav_buttons["settings"], Qt.MouseButton.LeftButton)
        window._settings_view.tg_chat_edit.setText("999000111")
        window._settings_view.tg_token_edit.setText(
            "123456789:AAH_e2e_test_bot_token_1234567890"
        )
        window._settings_view._save_telegram()
        qtbot.waitUntil(
            lambda: "Token zapisany bezpiecznie"
            in window._settings_view.tg_secret_status.text(),
            timeout=5000,
        )
        qtbot.mouseClick(window._settings_view.test_tg_btn, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: len(state["telegram_messages"]) == 1, timeout=5000)

        # 4. Reguła alertu + spadek ceny + wymuszenie sprawdzenia w zakładce Produkty
        api.create_alert(
            {
                "offer_id": offer_1_id,
                "rule_type": "target_price_below",
                "threshold_value": "2200.00",
                "cooldown_minutes": 0,
            }
        )
        state["xkom_price"] = "2150.00"

        qtbot.mouseClick(window._nav_buttons["products"], Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: window._products_view.table.rowCount() == 2, timeout=5000)
        window._products_view.table.selectRow(0)
        qtbot.mouseClick(window._products_view.btn_check_now, Qt.MouseButton.LeftButton)

        # Harmonogram pobiera nową cenę 2150.00 i wysyła alert Telegram
        qtbot.waitUntil(lambda: len(state["telegram_messages"]) == 2, timeout=10000)
        assert "2150.00 PLN" in state["telegram_messages"][1]

        # 5. Historia cen: przeklikanie wszystkich 6 zakresów i porównanie sklepów
        qtbot.mouseClick(window._nav_buttons["history"], Qt.MouseButton.LeftButton)
        qtbot.waitUntil(
            lambda: window._history_view.points_table.rowCount() >= 2,
            timeout=5000,
        )

        x_spans: dict[str, float] = {}
        for range_key in ("24h", "7d", "30d", "90d", "180d", "all"):
            btn = window._history_view._range_buttons[range_key]
            qtbot.mouseClick(btn, Qt.MouseButton.LeftButton)
            qtbot.wait(120)
            x_min, x_max = window._history_view.chart._plot.viewRange()[0]
            x_spans[range_key] = float(x_max - x_min)

        assert x_spans["24h"] < x_spans["7d"] < x_spans["30d"] < x_spans["180d"]

        window._history_view.compare_check.setChecked(True)
        qtbot.waitUntil(
            lambda: "Porównywane oferty: 2" in window._history_view.stats_strip.text(),
            timeout=5000,
        )

        # 6. Przeklikanie pozostałych zakładek: Powiadomienia, Sklepy, Zadania, Logi
        qtbot.mouseClick(window._nav_buttons["notifications"], Qt.MouseButton.LeftButton)
        qtbot.waitUntil(
            lambda: window._notifications_view.rules_table.rowCount() == 1
            and window._notifications_view.logs_table.rowCount() >= 2,
            timeout=5000,
        )

        qtbot.mouseClick(window._nav_buttons["stores"], Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: window._stores_view.table.rowCount() == 2, timeout=5000)

        qtbot.mouseClick(window._nav_buttons["jobs"], Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: window._jobs_view.table.rowCount() >= 1, timeout=5000)

        qtbot.mouseClick(window._nav_buttons["logs"], Qt.MouseButton.LeftButton)
        qtbot.wait(200)

        # 7. Przełączenie motywu Dark -> Light -> Dark
        qtbot.mouseClick(window._theme_btn, Qt.MouseButton.LeftButton)
        assert window._palette.name == "light"
        qtbot.mouseClick(window._theme_btn, Qt.MouseButton.LeftButton)
        assert window._palette.name == "dark"

        # 8. Usunięcie obu ofert czyści osierocony produkt na Dashboardzie
        api.delete_offer(offer_1_id)
        api.delete_offer(offer_2_id)
        qtbot.mouseClick(window._nav_buttons["dashboard"], Qt.MouseButton.LeftButton)
        qtbot.waitUntil(
            lambda: "Produkty: 0" in window._dashboard_view.metrics_strip.text(),
            timeout=5000,
        )
    finally:
        window.close()
        pool = QThreadPool.globalInstance()
        if pool is not None:
            pool.waitForDone(3000)
        qtbot.wait(50)
