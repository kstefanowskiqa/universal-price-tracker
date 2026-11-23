from __future__ import annotations

import json
from collections import deque
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from price_tracker.api.schemas import (
    AppSettingsResponse,
    AppSettingsUpdateRequest,
    LogEntryResponse,
)
from price_tracker.api.security import require_api_token
from price_tracker.config.settings import Settings
from price_tracker.db.models import AppSetting
from price_tracker.db.session import DatabaseManager
from price_tracker.scheduler.worker import MonitoringScheduler
from price_tracker.services.catalog import CatalogService

router = APIRouter(
    prefix="/api/v1/system",
    dependencies=[Depends(require_api_token)],
    tags=["system"],
)


async def _load_db_settings(db: DatabaseManager) -> dict[str, Any]:
    async with db.session() as session:
        rows = list((await session.execute(select(AppSetting))).scalars().all())
    return {row.key: row.value_json for row in rows}


@router.get("/settings", response_model=AppSettingsResponse)
async def get_app_settings(request: Request) -> AppSettingsResponse:
    cfg: Settings = request.app.state.settings
    db: DatabaseManager = request.app.state.db_manager
    stored = await _load_db_settings(db)

    interval = int(
        stored.get("default_check_interval_seconds", cfg.default_check_interval_seconds)
    )
    raw_theme = str(stored.get("theme", "dark"))
    theme: Literal["dark", "light"] = "light" if raw_theme == "light" else "dark"

    return AppSettingsResponse(
        default_check_interval_seconds=interval,
        max_concurrent_http_checks=cfg.max_concurrent_http_checks,
        max_concurrent_browser_checks=cfg.max_concurrent_browser_checks,
        job_history_retention_days=cfg.job_history_retention_days,
        theme=theme,
    )


@router.put("/settings", response_model=AppSettingsResponse)
async def update_app_settings(
    payload: AppSettingsUpdateRequest,
    request: Request,
) -> AppSettingsResponse:
    db: DatabaseManager = request.app.state.db_manager
    scheduler: MonitoringScheduler = request.app.state.scheduler
    catalog: CatalogService = request.app.state.catalog_service

    updates: dict[str, Any] = {}
    if payload.default_check_interval_seconds is not None:
        updates["default_check_interval_seconds"] = payload.default_check_interval_seconds
        scheduler.set_default_interval(payload.default_check_interval_seconds)
        catalog.set_default_interval(payload.default_check_interval_seconds)
    if payload.theme is not None:
        updates["theme"] = payload.theme

    if updates:
        async with db.session() as session:
            for key, value in updates.items():
                existing = await session.get(AppSetting, key)
                if existing is None:
                    session.add(AppSetting(key=key, value_json=value))
                else:
                    existing.value_json = value

    return await get_app_settings(request)


@router.get("/logs", response_model=list[LogEntryResponse])
async def get_system_logs(
    request: Request,
    level: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[LogEntryResponse]:
    cfg: Settings = request.app.state.settings
    log_file = cfg.resolved_log_dir / "price_tracker.jsonl"
    if not log_file.exists():
        return []

    raw_lines: deque[str] = deque(maxlen=limit * 4)
    with log_file.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped:
                raw_lines.append(stripped)

    level_filter = level.strip().lower() if level and level.strip() else None
    search_needle = search.strip().lower() if search and search.strip() else None

    entries: list[LogEntryResponse] = []
    for line in reversed(raw_lines):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            obj = {"event": line, "level": "info"}

        entry_level = str(obj.get("level") or "info").lower()
        if level_filter and level_filter != "all" and entry_level != level_filter:
            continue

        if search_needle and search_needle not in line.lower():
            continue

        details = {
            k: v
            for k, v in obj.items()
            if k not in {"timestamp", "level", "logger", "event"}
        }
        entries.append(
            LogEntryResponse(
                timestamp=str(obj.get("timestamp")) if obj.get("timestamp") else None,
                level=entry_level,
                logger=str(obj.get("logger")) if obj.get("logger") else None,
                event=str(obj.get("event") or ""),
                details=details,
            )
        )
        if len(entries) >= limit:
            break

    return entries
