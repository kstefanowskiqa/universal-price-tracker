from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from price_tracker.config.settings import Settings, get_settings


class LocalBackendManager:
    """Zarządza cyklem życia lokalnego serwera FastAPI uruchamianego z aplikacji desktopowej."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self._settings.api_host}:{self._settings.api_port}"

    @property
    def api_token(self) -> str:
        return self._settings.resolve_api_token()

    def is_healthy(self, *, timeout: float = 1.5) -> bool:
        try:
            response = httpx.get(f"{self.base_url}/health", timeout=timeout)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def ensure_database_migrated(self) -> None:
        """Wykonuje migracje Alembic przed uruchomieniem lokalnego backendu."""
        alembic_ini = self._find_alembic_ini()
        if alembic_ini is None:
            return
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(alembic_ini))
        bundled_migrations = alembic_ini.parent / "alembic_migrations"
        if bundled_migrations.exists():
            cfg.set_main_option("script_location", str(bundled_migrations))
        else:
            script_loc = cfg.get_main_option("script_location")
            if script_loc:
                loc_path = Path(script_loc)
                if not loc_path.is_absolute():
                    cfg.set_main_option(
                        "script_location",
                        str((alembic_ini.parent / loc_path).resolve()),
                    )
        cfg.set_main_option("sqlalchemy.url", self._settings.resolved_database_url)
        command.upgrade(cfg, "head")

    def ensure_started(self, *, startup_timeout_seconds: float = 15.0) -> bool:
        """Upewnia się, że lokalny backend odpowiada; w razie potrzeby uruchamia go w tle."""
        if self.is_healthy():
            return True

        env = os.environ.copy()
        env["PT_ENVIRONMENT"] = self._settings.environment
        env["PT_API_HOST"] = self._settings.api_host
        env["PT_API_PORT"] = str(self._settings.api_port)
        env["PT_API_TOKEN"] = self.api_token
        env["PT_DATA_DIR"] = str(self._settings.resolved_data_dir)
        env["PT_LOG_DIR"] = str(self._settings.resolved_log_dir)
        env["PT_CONFIG_DIR"] = str(self._settings.resolved_config_dir)
        if self._settings.database_url:
            env["PT_DATABASE_URL"] = self._settings.database_url

        self.ensure_database_migrated()

        env = os.environ.copy()
        env["PT_API_HOST"] = self._settings.api_host
        env["PT_API_PORT"] = str(self._settings.api_port)
        env["PT_API_TOKEN"] = self.api_token

        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--run-server"]
        else:
            cmd = [sys.executable, "-m", "price_tracker.server"]

        self._process = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )

        deadline = time.monotonic() + startup_timeout_seconds
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                return False
            if self.is_healthy(timeout=1.0):
                return True
            time.sleep(0.25)

        return False

    def stop(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None

    @staticmethod
    def _find_alembic_ini() -> Path | None:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            frozen_candidate = Path(str(meipass)) / "alembic.ini"
            if frozen_candidate.exists():
                return frozen_candidate
        cwd_candidate = Path.cwd() / "alembic.ini"
        if cwd_candidate.exists():
            return cwd_candidate
        pkg_root = Path(__file__).resolve().parents[3] / "alembic.ini"
        if pkg_root.exists():
            return pkg_root
        return None
