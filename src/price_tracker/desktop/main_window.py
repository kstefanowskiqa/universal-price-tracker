from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, QTimer
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from price_tracker.desktop.client import ApiClient, run_in_background
from price_tracker.desktop.config import DesktopConfig, DesktopConfigStore
from price_tracker.desktop.theme import (
    ThemeName,
    build_stylesheet,
    get_palette,
    make_svg_icon,
)
from price_tracker.desktop.views import (
    DashboardView,
    HistoryView,
    JobsView,
    LogsView,
    NotificationsView,
    ProductsView,
    SettingsView,
    StoresView,
)


def _resolve_assets_dir() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(str(meipass)) / "price_tracker" / "desktop" / "assets"
        if candidate.exists():
            return candidate
    return Path(__file__).resolve().parent / "assets"


ASSETS_DIR = _resolve_assets_dir()

NAV_ITEMS: list[tuple[str, str]] = [
    ("dashboard", "Przegląd"),
    ("products", "Produkty"),
    ("history", "Historia cen"),
    ("notifications", "Powiadomienia"),
    ("stores", "Sklepy"),
    ("jobs", "Zadania"),
    ("settings", "Ustawienia"),
    ("logs", "Logi"),
]


class MainWindow(QMainWindow):
    """Główne okno desktopowe: nawigacja boczna, 8 widoków roboczych i pasek statusu."""

    def __init__(
        self,
        api: ApiClient,
        config_store: DesktopConfigStore,
        desktop_config: DesktopConfig,
    ) -> None:
        super().__init__()
        self._api = api
        self._config_store = config_store
        self._desktop_config = desktop_config
        self._palette = get_palette(desktop_config.theme)

        self.setWindowTitle("Universal Price Tracker")
        self.resize(desktop_config.window_width, desktop_config.window_height)
        self.setMinimumSize(980, 640)

        central = QWidget()
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Wąski pasek boczny nawigacji (200px)
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(200)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(10, 14, 10, 12)
        side_layout.setSpacing(4)

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(6, 4, 6, 4)
        brand_row.setSpacing(8)
        self._brand_icon = QLabel()
        self._brand_icon.setFixedSize(24, 24)
        brand_row.addWidget(self._brand_icon)

        brand = QLabel("Price Tracker")
        brand.setObjectName("AppTitle")
        brand_row.addWidget(brand)
        brand_row.addStretch(1)
        side_layout.addLayout(brand_row)
        side_layout.addSpacing(8)

        self._nav_buttons: dict[str, QPushButton] = {}
        for key, label in NAV_ITEMS:
            btn = QPushButton(f"  {label}")
            btn.setObjectName("NavButton")
            btn.setIconSize(QSize(16, 16))
            btn.clicked.connect(lambda _checked=False, k=key: self.navigate(k))
            side_layout.addWidget(btn)
            self._nav_buttons[key] = btn

        side_layout.addStretch(1)
        self._theme_btn = QPushButton("Przełącz motyw")
        self._theme_btn.clicked.connect(self._toggle_theme)
        side_layout.addWidget(self._theme_btn)
        root_layout.addWidget(sidebar)

        # Obszar roboczy (8 sekcji)
        self._stack = QStackedWidget()
        self._dashboard_view = DashboardView(
            api,
            self._palette,
            on_quick_add=self._quick_add_url,
            on_open_history=self._open_history_for_offer,
        )
        self._products_view = ProductsView(
            api,
            self._palette,
            on_open_history=self._open_history_for_offer,
            on_status=self.show_status,
        )
        self._history_view = HistoryView(api, self._palette)
        self._notifications_view = NotificationsView(
            api, self._palette, on_status=self.show_status
        )
        self._stores_view = StoresView(api, self._palette, on_status=self.show_status)
        self._jobs_view = JobsView(api, self._palette, on_status=self.show_status)
        self._settings_view = SettingsView(
            api,
            config_store,
            desktop_config,
            on_theme_changed=self.apply_theme,
            on_status=self.show_status,
        )
        self._logs_view = LogsView(api, self._palette, on_status=self.show_status)

        self._views: dict[str, QWidget] = {
            "dashboard": self._dashboard_view,
            "products": self._products_view,
            "history": self._history_view,
            "notifications": self._notifications_view,
            "stores": self._stores_view,
            "jobs": self._jobs_view,
            "settings": self._settings_view,
            "logs": self._logs_view,
        }
        for key, _ in NAV_ITEMS:
            self._stack.addWidget(self._views[key])

        root_layout.addWidget(self._stack, stretch=1)
        self.setCentralWidget(central)

        # Pasek statusu
        status_bar = QStatusBar()
        self.setStatusBar(status_bar)
        self._conn_status = QLabel(f"API: {api.base_url}")
        status_bar.addPermanentWidget(self._conn_status)

        self._current_section = "dashboard"
        self.apply_theme(desktop_config.theme)
        self.navigate("dashboard")

        # Dyskretny timer okresowego odświeżania aktywnego widoku i statusu kolejki
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(desktop_config.auto_refresh_seconds * 1000)
        self._refresh_timer.timeout.connect(self._periodic_refresh)
        self._refresh_timer.start()

    def _update_brand_logo(self) -> None:
        logo_name = "logo_light.svg" if self._palette.name == "light" else "logo_dark.svg"
        svg_path = ASSETS_DIR / logo_name
        if svg_path.exists():
            self._brand_icon.setPixmap(QIcon(str(svg_path)).pixmap(24, 24))

    def apply_theme(self, theme_name: ThemeName) -> None:
        self._palette = get_palette(theme_name)
        self.setStyleSheet(build_stylesheet(self._palette))
        self._update_brand_logo()

        for key, btn in self._nav_buttons.items():
            icon_color = (
                self._palette.text_primary
                if key == self._current_section
                else self._palette.text_secondary
            )
            btn.setIcon(make_svg_icon(key, icon_color, 16))

        self._dashboard_view.apply_palette(self._palette)
        self._products_view.apply_palette(self._palette)
        self._history_view.apply_palette(self._palette)
        self._notifications_view.apply_palette(self._palette)
        self._stores_view.apply_palette(self._palette)
        self._jobs_view.apply_palette(self._palette)
        self._logs_view.apply_palette(self._palette)
        self._theme_btn.setText("Tryb jasny" if theme_name == "dark" else "Tryb ciemny")

    def _toggle_theme(self) -> None:
        next_theme: ThemeName = "light" if self._palette.name == "dark" else "dark"
        self._desktop_config.theme = next_theme
        self._config_store.save(self._desktop_config)
        self.apply_theme(next_theme)

    def navigate(self, section_key: str) -> None:
        if section_key not in self._views:
            return
        self._current_section = section_key
        widget = self._views[section_key]
        self._stack.setCurrentWidget(widget)

        for key, btn in self._nav_buttons.items():
            active = key == section_key
            btn.setProperty("active", active)
            btn.setIcon(
                make_svg_icon(
                    key,
                    self._palette.text_primary if active else self._palette.text_secondary,
                    16,
                )
            )
            style = btn.style()
            if style is not None:
                style.unpolish(btn)
                style.polish(btn)

        self._refresh_current_view()

    def _refresh_current_view(self) -> None:
        current = self._views[self._current_section]
        refresh_fn = getattr(current, "refresh", None)
        if callable(refresh_fn):
            refresh_fn()
        self._poll_scheduler_indicator()

    def _periodic_refresh(self) -> None:
        if self._current_section in {"dashboard", "jobs", "products"}:
            self._refresh_current_view()
        else:
            self._poll_scheduler_indicator()

    def _poll_scheduler_indicator(self) -> None:
        def _ok(st: dict[str, Any]) -> None:
            mode = "Wstrzymany" if st["is_paused"] else "Aktywny"
            self._conn_status.setText(
                f"Harmonogram: {mode} · W kolejce: {st['pending_jobs']} · {self._api.base_url}"
            )

        run_in_background(
            self._api.get_scheduler_status,
            on_success=_ok,
            on_error=lambda _: self._conn_status.setText(
                f"Brak połączenia z backendem ({self._api.base_url})"
            ),
        )

    def _quick_add_url(self, url: str) -> None:
        self.navigate("products")
        self._products_view.open_add_dialog(initial_url=url)

    def _open_history_for_offer(self, product_id: int, offer_id: int) -> None:
        self._current_section = "history"
        self._stack.setCurrentWidget(self._history_view)
        for key, btn in self._nav_buttons.items():
            active = key == "history"
            btn.setProperty("active", active)
            style = btn.style()
            if style is not None:
                style.unpolish(btn)
                style.polish(btn)
        self._history_view.select_offer(product_id, offer_id)

    def show_status(self, message: str) -> None:
        bar = self.statusBar()
        if bar is not None:
            bar.showMessage(message, 6000)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._refresh_timer.stop()
        self._desktop_config.window_width = self.width()
        self._desktop_config.window_height = self.height()
        self._config_store.save(self._desktop_config)
        super().closeEvent(event)
