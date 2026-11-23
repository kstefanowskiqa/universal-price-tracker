from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from price_tracker.config.settings import get_settings
from price_tracker.desktop.client import ApiClient
from price_tracker.desktop.config import DesktopConfigStore
from price_tracker.desktop.launcher import LocalBackendManager
from price_tracker.desktop.main_window import MainWindow


def _resolve_assets_dir() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(str(meipass)) / "price_tracker" / "desktop" / "assets"
        if candidate.exists():
            return candidate
    return Path(__file__).resolve().parent / "assets"


ASSETS_DIR = _resolve_assets_dir()


def _configure_windows_taskbar_id() -> None:
    if sys.platform != "win32":
        return
    windll = getattr(ctypes, "windll", None)
    if windll is not None:
        windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "UniversalPriceTracker.Desktop.App"
        )


def main() -> None:
    """Punkt wejścia aplikacji desktopowej Universal Price Tracker."""
    _configure_windows_taskbar_id()

    app = QApplication(sys.argv)
    app.setApplicationName("Universal Price Tracker")
    app.setOrganizationName("UniversalPriceTracker")

    ico_path = ASSETS_DIR / "upt_logo.ico"
    if ico_path.exists():
        app.setWindowIcon(QIcon(str(ico_path)))

    settings = get_settings()
    config_store = DesktopConfigStore(settings=settings)
    desktop_cfg = config_store.load()

    backend_manager: LocalBackendManager | None = None

    if desktop_cfg.connection_mode == "remote" and desktop_cfg.remote_url:
        base_url = desktop_cfg.remote_url
        api_token = config_store.get_remote_token() or ""
    else:
        backend_manager = LocalBackendManager(settings=settings)
        if not backend_manager.ensure_started():
            QMessageBox.critical(
                None,
                "Błąd uruchamiania backendu",
                "Nie udało się uruchomić lokalnego serwera backendowego.\n"
                "Sprawdź logi aplikacji w katalogu %LOCALAPPDATA%\\UniversalPriceTracker\\Logs.",
            )
            sys.exit(1)
        base_url = backend_manager.base_url
        api_token = backend_manager.api_token

    api = ApiClient(base_url=base_url, api_token=api_token)
    window = MainWindow(api=api, config_store=config_store, desktop_config=desktop_cfg)

    if backend_manager is not None:
        app.aboutToQuit.connect(backend_manager.stop)

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
