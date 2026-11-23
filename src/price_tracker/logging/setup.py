from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from typing import Any

import structlog
from structlog.typing import EventDict, WrappedLogger

from price_tracker.config.settings import Settings

_SENSITIVE_KEYS = frozenset(
    {"token", "api_token", "bot_token", "password", "secret", "authorization", "x-api-token"}
)
_TELEGRAM_TOKEN_RE = re.compile(
    r"(?:(?<=bot)|(?<![A-Za-z0-9]))\d{8,12}:[A-Za-z0-9_-]{30,60}(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)


def _scrub_string(value: str) -> str:
    return _TELEGRAM_TOKEN_RE.sub("[REDACTED_TELEGRAM_TOKEN]", value)


def _scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        return _scrub_string(value)
    if isinstance(value, dict):
        return {
            k: ("[REDACTED]" if str(k).lower() in _SENSITIVE_KEYS else _scrub_value(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub_value(item) for item in value]
    return value


def redact_secrets_processor(
    _logger: WrappedLogger, _method_name: str, event_dict: EventDict
) -> EventDict:
    """Maskuje klucze wrażliwe oraz wzorce tokenów Telegrama przed zapisem do logów."""
    sanitized: EventDict = {}
    for key, value in event_dict.items():
        if key.lower() in _SENSITIVE_KEYS or "token" in key.lower() or "secret" in key.lower():
            sanitized[key] = "[REDACTED]"
        else:
            sanitized[key] = _scrub_value(value)
    return sanitized


def configure_logging(settings: Settings) -> None:
    """Konfiguruje structlog oraz standardowy moduł logging (konsola + opcjonalny plik JSON)."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_secrets_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    console_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
        ],
    )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    if settings.log_json_file and settings.environment != "test":
        log_file = settings.resolved_log_dir / "price_tracker.jsonl"
        file_formatter = structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(ensure_ascii=False),
            ],
        )
        file_handler = RotatingFileHandler(
            filename=log_file,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

    # Wyciszenie zbyt głośnych loggerów bibliotek trzecich
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.stdlib.get_logger(name)
