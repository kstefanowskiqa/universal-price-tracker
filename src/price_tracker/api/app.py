from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse

from price_tracker import __version__
from price_tracker.api.routers import (
    analytics_router,
    monitoring_router,
    notifications_router,
    products_router,
    system_router,
)
from price_tracker.api.schemas import HealthResponse, SystemInfoResponse
from price_tracker.api.security import require_api_token
from price_tracker.config.secrets import SecretStore
from price_tracker.config.settings import Settings, get_settings
from price_tracker.core.exceptions import ConflictError, NotFoundError, ValidationError
from price_tracker.db.session import DatabaseManager
from price_tracker.logging.setup import configure_logging, get_logger
from price_tracker.notifications.service import NotificationService
from price_tracker.scheduler.domain_limiter import DomainRateLimiter
from price_tracker.scheduler.worker import MonitoringScheduler
from price_tracker.scraping.browser import BrowserFetcher
from price_tracker.scraping.engine import ScrapingEngine
from price_tracker.services.analytics import AnalyticsService
from price_tracker.services.catalog import CatalogService

logger = get_logger(__name__)


def _detect_dialect(db_url: str) -> Literal["sqlite", "postgresql", "other"]:
    if db_url.startswith("sqlite"):
        return "sqlite"
    if db_url.startswith("postgresql"):
        return "postgresql"
    return "other"


def create_app(
    settings: Settings | None = None,
    db_manager: DatabaseManager | None = None,
    scraping_engine: ScrapingEngine | None = None,
    scheduler: MonitoringScheduler | None = None,
    notification_service: NotificationService | None = None,
    secret_store: SecretStore | None = None,
    *,
    start_scheduler: bool | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    owns_db_manager = db_manager is None
    owns_engine = scraping_engine is None
    owns_notifications = notification_service is None

    manager = db_manager or DatabaseManager.from_settings(app_settings)
    engine = scraping_engine or ScrapingEngine(
        browser_fetcher=BrowserFetcher(max_concurrent=app_settings.max_concurrent_browser_checks),
        enable_browser_fallback=True,
    )
    notif_svc = notification_service or NotificationService(
        manager,
        secret_store=secret_store,
    )
    limiter = DomainRateLimiter(max_global_concurrent=app_settings.max_concurrent_http_checks)
    active_scheduler = scheduler or MonitoringScheduler(
        manager,
        engine,
        rate_limiter=limiter,
        default_interval_seconds=app_settings.default_check_interval_seconds,
        job_retention_days=app_settings.job_history_retention_days,
        on_check_completed=notif_svc.handle_price_check_event,
    )
    catalog_service = CatalogService(
        manager,
        engine,
        default_check_interval_seconds=app_settings.default_check_interval_seconds,
    )
    analytics_service = AnalyticsService(manager)
    resolved_token = app_settings.resolve_api_token()
    should_start_scheduler = (
        start_scheduler if start_scheduler is not None else app_settings.environment != "test"
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        configure_logging(app_settings)
        logger.info(
            "backend_starting",
            version=__version__,
            environment=app_settings.environment,
            dialect=_detect_dialect(app_settings.resolved_database_url),
        )
        if should_start_scheduler:
            await active_scheduler.start()
        yield
        logger.info("backend_stopping")
        await active_scheduler.stop()
        if owns_notifications:
            await notif_svc.close()
        if owns_engine:
            await engine.close()
        if owns_db_manager:
            await manager.dispose()

    app = FastAPI(
        title="Universal Price Tracker API",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.db_manager = manager
    app.state.scraping_engine = engine
    app.state.scheduler = active_scheduler
    app.state.notification_service = notif_svc
    app.state.catalog_service = catalog_service
    app.state.analytics_service = analytics_service
    app.state.api_token = resolved_token

    @app.exception_handler(NotFoundError)
    async def _handle_not_found(_request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def _handle_conflict(_request: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})

    @app.exception_handler(ValidationError)
    async def _handle_validation(_request: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": str(exc)},
        )

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def healthcheck(request: Request) -> JSONResponse:
        db: DatabaseManager = request.app.state.db_manager
        db_ok = await db.check_health()
        payload = HealthResponse(
            status="ok" if db_ok else "degraded",
            version=__version__,
            database="ok" if db_ok else "error",
            timestamp_utc=datetime.now(UTC),
        )
        http_status = 200 if db_ok else 503
        return JSONResponse(status_code=http_status, content=payload.model_dump(mode="json"))

    @app.get(
        "/api/v1/system/info",
        response_model=SystemInfoResponse,
        dependencies=[Depends(require_api_token)],
        tags=["system"],
    )
    async def system_info(request: Request) -> SystemInfoResponse:
        cfg: Settings = request.app.state.settings
        return SystemInfoResponse(
            version=__version__,
            environment=cfg.environment,
            database_dialect=_detect_dialect(cfg.resolved_database_url),
            default_check_interval_seconds=cfg.default_check_interval_seconds,
            max_concurrent_http_checks=cfg.max_concurrent_http_checks,
            max_concurrent_browser_checks=cfg.max_concurrent_browser_checks,
        )

    app.include_router(products_router)
    app.include_router(analytics_router)
    app.include_router(monitoring_router)
    app.include_router(notifications_router)
    app.include_router(system_router)

    return app
