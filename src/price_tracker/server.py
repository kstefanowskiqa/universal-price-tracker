from __future__ import annotations

import uvicorn

from price_tracker.api.app import create_app
from price_tracker.config.settings import get_settings


def main() -> None:
    """Punkt wejścia serwera backendowego (Windows 11 / Linux / Docker)."""

    settings = get_settings()
    app = create_app(settings=settings)

    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,  # logowanie zarządzane w całości przez structlog
    )


if __name__ == "__main__":
    main()
