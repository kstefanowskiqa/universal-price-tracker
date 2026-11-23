from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from price_tracker.config.secrets import SecretStore, get_secret_store
from price_tracker.config.settings import Settings, get_settings

REMOTE_TOKEN_SECRET_KEY = "remote_api_token"


@dataclass
class DesktopConfig:
    """Lokalne ustawienia klienta desktopowego (tryb połączenia, motyw, geometria okna)."""

    connection_mode: Literal["local", "remote"] = "local"
    remote_url: str = ""
    theme: Literal["dark", "light"] = "dark"
    auto_refresh_seconds: int = 30
    window_width: int = 1320
    window_height: int = 840


class DesktopConfigStore:
    def __init__(
        self,
        config_path: Path | None = None,
        secret_store: SecretStore | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._path = config_path or (self._settings.resolved_config_dir / "desktop_client.json")
        self._secrets = secret_store or get_secret_store()

    def load(self) -> DesktopConfig:
        if not self._path.exists():
            return DesktopConfig()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return DesktopConfig()

        mode: Literal["local", "remote"] = (
            "remote" if raw.get("connection_mode") == "remote" else "local"
        )
        theme: Literal["dark", "light"] = (
            "light" if raw.get("theme") == "light" else "dark"
        )
        return DesktopConfig(
            connection_mode=mode,
            remote_url=str(raw.get("remote_url") or ""),
            theme=theme,
            auto_refresh_seconds=max(5, int(raw.get("auto_refresh_seconds") or 30)),
            window_width=max(960, int(raw.get("window_width") or 1320)),
            window_height=max(640, int(raw.get("window_height") or 840)),
        )

    def save(self, config: DesktopConfig) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(asdict(config), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def get_remote_token(self) -> str | None:
        return self._secrets.get_secret(REMOTE_TOKEN_SECRET_KEY)

    def set_remote_token(self, token: str) -> None:
        self._secrets.set_secret(REMOTE_TOKEN_SECRET_KEY, token)
