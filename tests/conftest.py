from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from price_tracker.api.app import create_app
from price_tracker.config.settings import Settings
from price_tracker.db.base import Base
from price_tracker.db.session import DatabaseManager


@pytest.fixture()
def test_settings(tmp_path: Path) -> Settings:
    db_path = tmp_path / "test_price_tracker.db"
    return Settings(
        environment="test",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        config_dir=tmp_path / "config",
        database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}",
        api_token="test-token-value-1234567890abcdef1234567890",
        log_json_file=False,
    )


@pytest.fixture()
async def db_manager(test_settings: Settings) -> AsyncIterator[DatabaseManager]:
    manager = DatabaseManager.from_settings(test_settings)
    async with manager.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield manager
    finally:
        await manager.dispose()


@pytest.fixture()
async def api_client(
    test_settings: Settings, db_manager: DatabaseManager
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings=test_settings, db_manager=db_manager)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
