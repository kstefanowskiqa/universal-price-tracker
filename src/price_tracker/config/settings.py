from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from platformdirs import user_config_dir, user_data_dir, user_log_dir
from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "UniversalPriceTracker"
APP_AUTHOR = "UniversalPriceTracker"


class Settings(BaseSettings):
    """Centralna konfiguracja aplikacji (.env, zmienne środowiskowe i ścieżki OS)."""

    model_config = SettingsConfigDict(
        env_prefix="PT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["local", "server", "test"] = "local"

    # Katalogi (puste -> automatyczne ścieżki systemowe, np. %LOCALAPPDATA%\UniversalPriceTracker)
    data_dir: Path | None = None
    log_dir: Path | None = None
    config_dir: Path | None = None

    # Baza danych
    database_url: str | None = None
    database_echo: bool = False

    # Serwer API
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8765, ge=1024, le=65535)
    api_token: str | None = None

    # Logowanie
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json_file: bool = True
    log_max_bytes: int = 10 * 1024 * 1024  # 10 MB
    log_backup_count: int = 5

    # Domyślne parametry monitorowania (używane od Etapu 2)
    default_check_interval_seconds: int = Field(default=3600, ge=300, le=86400 * 7)
    max_concurrent_http_checks: int = Field(default=5, ge=1, le=50)
    max_concurrent_browser_checks: int = Field(default=2, ge=1, le=10)
    job_history_retention_days: int = Field(default=30, ge=1, le=365)

    @field_validator("database_url", "api_token", mode="before")
    @classmethod
    def _empty_str_to_none(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved_data_dir(self) -> Path:
        path = self.data_dir or Path(user_data_dir(APP_NAME, APP_AUTHOR))
        path.mkdir(parents=True, exist_ok=True)
        return path

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved_log_dir(self) -> Path:
        path = self.log_dir or Path(user_log_dir(APP_NAME, APP_AUTHOR))
        path.mkdir(parents=True, exist_ok=True)
        return path

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved_config_dir(self) -> Path:
        path = self.config_dir or Path(user_config_dir(APP_NAME, APP_AUTHOR))
        path.mkdir(parents=True, exist_ok=True)
        return path

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        db_file = (self.resolved_data_dir / "price_tracker.db").resolve()
        return f"sqlite+aiosqlite:///{db_file.as_posix()}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_sqlite(self) -> bool:
        return self.resolved_database_url.startswith("sqlite")

    def resolve_api_token(self) -> str:
        """Zwraca skonfigurowany token lub generuje bezpieczny token sesyjny dla trybu lokalnego."""
        if self.api_token:
            return self.api_token
        if self.environment == "server":
            raise ValueError(
                "W trybie 'server' wymagane jest jawne ustawienie zmiennej PT_API_TOKEN."
            )
        token_file = self.resolved_config_dir / ".local_api_token"
        if token_file.exists():
            stored = token_file.read_text(encoding="utf-8").strip()
            if len(stored) >= 32:
                return stored
        generated = secrets.token_urlsafe(32)
        token_file.write_text(generated, encoding="utf-8")
        return generated


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
