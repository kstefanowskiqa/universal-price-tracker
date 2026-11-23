from __future__ import annotations

from pathlib import Path

import pytest

from price_tracker.config.secrets import EnvSecretStore
from price_tracker.config.settings import Settings
from price_tracker.core.exceptions import SecretStoreError
from price_tracker.logging.setup import redact_secrets_processor


def test_settings_default_sqlite_path_created(tmp_path: Path) -> None:
    settings = Settings(
        environment="local",
        data_dir=tmp_path / "app_data",
        config_dir=tmp_path / "app_cfg",
        log_dir=tmp_path / "app_logs",
    )
    assert settings.is_sqlite is True
    assert settings.resolved_database_url.endswith("price_tracker.db")
    assert settings.resolved_data_dir.exists()


def test_settings_local_token_is_persisted(tmp_path: Path) -> None:
    settings = Settings(
        environment="local",
        config_dir=tmp_path / "cfg",
    )
    first = settings.resolve_api_token()
    second = settings.resolve_api_token()
    assert len(first) >= 32
    assert first == second


def test_settings_server_mode_requires_explicit_token() -> None:
    settings = Settings(environment="server", api_token=None)
    with pytest.raises(ValueError, match="PT_API_TOKEN"):
        settings.resolve_api_token()


def test_env_secret_store_reads_and_blocks_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PT_SECRET_TELEGRAM_BOT_TOKEN", "123456789:ABCDEF_example_token_1234567890")
    store = EnvSecretStore()

    assert store.get_secret("telegram_bot_token") == "123456789:ABCDEF_example_token_1234567890"
    assert store.get_secret("nonexistent_key") is None

    with pytest.raises(SecretStoreError):
        store.set_secret("telegram_bot_token", "new-value")


def test_logging_redacts_sensitive_keys_and_telegram_tokens() -> None:
    raw_event = {
        "event": "telegram_request_failed",
        "bot_token": "999999999:should_not_leak",
        "url": "https://api.telegram.org/bot123456789:AAH_abcdefghijklmnopqrstuvwxyz12345/sendMessage",
        "nested": {"authorization": "Bearer xyz", "ok": "visible"},
    }
    redacted = redact_secrets_processor(None, "info", raw_event)  # type: ignore[arg-type]

    assert redacted["bot_token"] == "[REDACTED]"
    assert "AAH_abcdefghijklmnopqrstuvwxyz12345" not in str(redacted["url"])
    assert "[REDACTED_TELEGRAM_TOKEN]" in str(redacted["url"])
    assert redacted["nested"]["authorization"] == "[REDACTED]"
    assert redacted["nested"]["ok"] == "visible"
