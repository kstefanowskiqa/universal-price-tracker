from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from price_tracker.config.settings import Settings
from price_tracker.logging.setup import get_logger

logger = get_logger(__name__)


def _configure_sqlite_connection(dbapi_connection: Any, _connection_record: Any) -> None:
    """Wymusza tryb WAL, obsługę kluczy obcych i timeout przy blokadach SQLite."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


class DatabaseManager:
    """Zarządza pulą połączeń SQLAlchemy 2.0 (async) i cyklem życia sesji."""

    def __init__(self, database_url: str, *, echo: bool = False) -> None:
        self._database_url = database_url
        self._is_sqlite = database_url.startswith("sqlite")

        engine_kwargs: dict[str, Any] = {
            "echo": echo,
            "pool_pre_ping": True,
        }
        if self._is_sqlite:
            engine_kwargs["connect_args"] = {"timeout": 15}

        self.engine: AsyncEngine = create_async_engine(database_url, **engine_kwargs)
        if self._is_sqlite:
            event.listen(self.engine.sync_engine, "connect", _configure_sqlite_connection)

        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> DatabaseManager:
        return cls(
            database_url=settings.resolved_database_url,
            echo=settings.database_echo,
        )

    @property
    def is_sqlite(self) -> bool:
        return self._is_sqlite

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def check_health(self) -> bool:
        try:
            async with self.engine.connect() as conn:
                result = await conn.execute(text("SELECT 1"))
                return result.scalar() == 1
        except Exception:
            logger.exception("database_healthcheck_failed")
            return False

    async def dispose(self) -> None:
        await self.engine.dispose()
