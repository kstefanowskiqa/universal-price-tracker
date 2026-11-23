from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from price_tracker.desktop.client import ApiClient, run_in_background
from price_tracker.desktop.config import DesktopConfig, DesktopConfigStore
from price_tracker.desktop.theme import ThemeName
from price_tracker.desktop.widgets import INTERVAL_OPTIONS


class SettingsView(QWidget):
    """Ustawienia aplikacji: wygląd/harmonogram, Telegram Bot API oraz tryb lokalny/VPS."""

    def __init__(
        self,
        api: ApiClient,
        config_store: DesktopConfigStore,
        desktop_config: DesktopConfig,
        *,
        on_theme_changed: Callable[[ThemeName], None],
        on_status: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._config_store = config_store
        self._desktop_config = desktop_config
        self._on_theme_changed = on_theme_changed
        self._on_status = on_status

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        title = QLabel("Ustawienia")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        tabs = QTabWidget()

        # Karta 1: Ogólne i harmonogram
        general_tab = QWidget()
        gen_layout = QFormLayout(general_tab)
        gen_layout.setContentsMargins(16, 16, 16, 16)
        gen_layout.setSpacing(12)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Ciemny (Dark)", "dark")
        self.theme_combo.addItem("Jasny (Light)", "light")
        idx = self.theme_combo.findData(desktop_config.theme)
        self.theme_combo.setCurrentIndex(idx if idx >= 0 else 0)
        gen_layout.addRow("Motyw interfejsu:", self.theme_combo)

        self.interval_combo = QComboBox()
        for label, secs in INTERVAL_OPTIONS:
            if secs is not None:
                self.interval_combo.addItem(label, secs)
        gen_layout.addRow("Domyślna częstotliwość sprawdzania:", self.interval_combo)

        self.info_label = QLabel("Ładowanie informacji o środowisku...")
        self.info_label.setObjectName("MutedText")
        gen_layout.addRow("Środowisko backendu:", self.info_label)

        save_gen_btn = QPushButton("Zapisz ustawienia ogólne")
        save_gen_btn.setObjectName("PrimaryButton")
        save_gen_btn.clicked.connect(self._save_general)
        gen_layout.addRow("", save_gen_btn)
        tabs.addTab(general_tab, "Ogólne i harmonogram")

        # Karta 2: Telegram Bot API
        tg_tab = QWidget()
        tg_layout = QFormLayout(tg_tab)
        tg_layout.setContentsMargins(16, 16, 16, 16)
        tg_layout.setSpacing(12)

        self.tg_chat_edit = QLineEdit()
        self.tg_chat_edit.setPlaceholderText("np. 123456789 (ID czatu lub grupy)")
        tg_layout.addRow("Chat ID:", self.tg_chat_edit)

        self.tg_token_edit = QLineEdit()
        self.tg_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.tg_token_edit.setPlaceholderText(
            "Wpisz token bota od @BotFather (zapisywany bezpiecznie w Windows Credential Manager)"
        )
        tg_layout.addRow("Token bota Telegrama:", self.tg_token_edit)

        self.tg_secret_status = QLabel("Status tokenu: brak danych")
        self.tg_secret_status.setObjectName("MutedText")
        tg_layout.addRow("Magazyn sekretów:", self.tg_secret_status)

        self.tg_enabled_check = QCheckBox("Powiadomienia Telegram włączone")
        self.tg_enabled_check.setChecked(True)
        tg_layout.addRow("", self.tg_enabled_check)

        tg_btns = QHBoxLayout()
        save_tg_btn = QPushButton("Zapisz konfigurację Telegram")
        save_tg_btn.setObjectName("PrimaryButton")
        save_tg_btn.clicked.connect(self._save_telegram)
        tg_btns.addWidget(save_tg_btn)

        self.test_tg_btn = QPushButton("Wyślij wiadomość testową")
        self.test_tg_btn.clicked.connect(self._test_telegram)
        tg_btns.addWidget(self.test_tg_btn)
        tg_btns.addStretch(1)
        tg_layout.addRow("", tg_btns)
        tabs.addTab(tg_tab, "Telegram Bot API")

        # Karta 3: Połączenie (Lokalny PC vs Zdalny serwer VPS / Docker)
        conn_tab = QWidget()
        conn_layout = QFormLayout(conn_tab)
        conn_layout.setContentsMargins(16, 16, 16, 16)
        conn_layout.setSpacing(12)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Lokalny backend na tym komputerze (Windows 11)", "local")
        self.mode_combo.addItem("Zdalny serwer backendowy (VPS / Linux / Docker)", "remote")
        midx = self.mode_combo.findData(desktop_config.connection_mode)
        self.mode_combo.setCurrentIndex(midx if midx >= 0 else 0)
        self.mode_combo.currentIndexChanged.connect(self._sync_conn_fields)
        conn_layout.addRow("Tryb pracy:", self.mode_combo)

        self.remote_url_edit = QLineEdit(desktop_config.remote_url)
        self.remote_url_edit.setPlaceholderText("https://tracker.twojadomena.pl")
        conn_layout.addRow("Adres zdalnego API:", self.remote_url_edit)

        self.remote_token_edit = QLineEdit()
        self.remote_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.remote_token_edit.setPlaceholderText("Token PT_API_TOKEN z serwera VPS")
        conn_layout.addRow("Token API serwera:", self.remote_token_edit)

        note = QLabel(
        "Zmiana trybu połączenia wymaga ponownego uruchomienia aplikacji desktopowej."
        )
        note.setObjectName("MutedText")
        conn_layout.addRow("", note)

        save_conn_btn = QPushButton("Zapisz ustawienia połączenia")
        save_conn_btn.clicked.connect(self._save_connection)
        conn_layout.addRow("", save_conn_btn)
        self._sync_conn_fields()
        tabs.addTab(conn_tab, "Połączenie / Serwer")

        root.addWidget(tabs, stretch=1)

    def refresh(self) -> None:
        def _work() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
            return (
                self._api.get_settings(),
                self._api.system_info(),
                self._api.list_channels(),
            )

        run_in_background(
            _work,
            on_success=self._render,
            on_error=lambda err: self._on_status(f"Błąd pobierania ustawień: {err}"),
        )

    def _render(
        self,
        data: tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]],
    ) -> None:
        app_cfg, sys_info, channels = data
        idx = self.interval_combo.findData(int(app_cfg["default_check_interval_seconds"]))
        if idx >= 0:
            self.interval_combo.setCurrentIndex(idx)

        self.info_label.setText(
            f"Wersja {sys_info['version']} · Baza: {sys_info['database_dialect']} · "
            f"Limity: {sys_info['max_concurrent_http_checks']} HTTP / "
            f"{sys_info['max_concurrent_browser_checks']} Playwright · URL: {self._api.base_url}"
        )

        tg = next((c for c in channels if c["channel_type"] == "telegram"), None)
        if tg is not None:
            self.tg_chat_edit.setText(str(tg.get("config_json", {}).get("chat_id") or ""))
            self.tg_enabled_check.setChecked(bool(tg.get("is_enabled", True)))
            self.tg_secret_status.setText(
                "Token zapisany bezpiecznie (zostaw pole puste, aby zachować obecny)."
                if tg.get("has_secret")
                else "Brak zapisanego tokenu bota."
            )
        else:
            self.tg_secret_status.setText("Kanał Telegram nie został jeszcze skonfigurowany.")

    def _save_general(self) -> None:
        theme: ThemeName = "light" if self.theme_combo.currentData() == "light" else "dark"
        interval = int(self.interval_combo.currentData())

        self._desktop_config.theme = theme
        self._config_store.save(self._desktop_config)
        self._on_theme_changed(theme)

        run_in_background(
            lambda: self._api.update_settings(
                {"default_check_interval_seconds": interval, "theme": theme}
            ),
            on_success=lambda _: self._on_status("Zapisano ustawienia ogólne."),
            on_error=lambda err: QMessageBox.warning(self, "Błąd zapisu", err),
        )

    def _save_telegram(self) -> None:
        chat_id = self.tg_chat_edit.text().strip()
        if not chat_id:
            QMessageBox.warning(self, "Brak Chat ID", "Podaj identyfikator czatu Telegram.")
            return
        token = self.tg_token_edit.text().strip() or None
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "bot_token": token,
            "is_enabled": self.tg_enabled_check.isChecked(),
        }

        def _ok(_res: Any) -> None:
            self.tg_token_edit.clear()
            self._on_status("Zapisano konfigurację Telegram Bot API.")
            self.refresh()

        run_in_background(
            lambda: self._api.configure_telegram(payload),
            on_success=_ok,
            on_error=lambda err: QMessageBox.critical(self, "Błąd konfiguracji", err),
        )

    def _test_telegram(self) -> None:
        self.test_tg_btn.setEnabled(False)

        def _ok(res: dict[str, Any]) -> None:
            self.test_tg_btn.setEnabled(True)
            if res.get("ok"):
                QMessageBox.information(
                    self, "Test Telegram", "Wiadomość testowa została wysłana pomyślnie."
                )
            else:
                QMessageBox.warning(
                    self,
                    "Test Telegram nieudany",
                    str(res.get("error_message") or "Nieznany błąd."),
                )

        def _err(msg: str) -> None:
            self.test_tg_btn.setEnabled(True)
            QMessageBox.critical(self, "Błąd testu", msg)

        run_in_background(self._api.test_telegram, on_success=_ok, on_error=_err)

    def _sync_conn_fields(self) -> None:
        is_remote = self.mode_combo.currentData() == "remote"
        self.remote_url_edit.setEnabled(is_remote)
        self.remote_token_edit.setEnabled(is_remote)

    def _save_connection(self) -> None:
        mode: Literal["local", "remote"] = (
          "remote" if self.mode_combo.currentData() == "remote" else "local"
        )
        self._desktop_config.connection_mode = mode
        self._desktop_config.remote_url = self.remote_url_edit.text().strip()
        self._config_store.save(self._desktop_config)
        token = self.remote_token_edit.text().strip()
        if token:
            self._config_store.set_remote_token(token)
            self.remote_token_edit.clear()
        self._on_status("Zapisano ustawienia połączenia (uruchom ponownie, aby zastosować).")
