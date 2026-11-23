from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

EXPECTED_TABLES = {
    "alembic_version",
    "app_settings",
    "alert_rules",
    "categories",
    "check_jobs",
    "notification_channels",
    "notification_logs",
    "price_history",
    "product_tags",
    "products",
    "stores",
    "tags",
    "tracked_offers",
}


def _list_sqlite_tables(db_file: Path) -> set[str]:
    conn = sqlite3.connect(db_file)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {row[0] for row in rows}
    finally:
        conn.close()


def test_alembic_upgrade_downgrade_upgrade_cycle(tmp_path: Path) -> None:
    db_file = tmp_path / "migration_test.db"
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_file.as_posix()}")

    # 1. Pełny upgrade
    command.upgrade(alembic_cfg, "head")
    assert _list_sqlite_tables(db_file) == EXPECTED_TABLES

    # 2. Pełny downgrade do bazy
    command.downgrade(alembic_cfg, "base")
    assert _list_sqlite_tables(db_file) == {"alembic_version"}

    # 3. Ponowny upgrade weryfikuje idempotentność schematu
    command.upgrade(alembic_cfg, "head")
    assert _list_sqlite_tables(db_file) == EXPECTED_TABLES
