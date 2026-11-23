from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from price_tracker.api.schemas import (
    AdapterInfoResponse,
    CheckJobResponse,
    EnqueueCheckResponse,
    SchedulerStatusResponse,
    StoreResponse,
    StoreUpdateRequest,
)
from price_tracker.api.security import require_api_token
from price_tracker.core.enums import CheckJobStatus
from price_tracker.scheduler.worker import MonitoringScheduler
from price_tracker.scraping.engine import ScrapingEngine
from price_tracker.services.catalog import CatalogService

router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_api_token)],
    tags=["monitoring"],
)


def _get_catalog(request: Request) -> CatalogService:
    svc: CatalogService = request.app.state.catalog_service
    return svc


def _get_scheduler(request: Request) -> MonitoringScheduler:
    sched: MonitoringScheduler = request.app.state.scheduler
    return sched


def _get_engine(request: Request) -> ScrapingEngine:
    eng: ScrapingEngine = request.app.state.scraping_engine
    return eng


@router.get("/stores", response_model=list[StoreResponse])
async def list_stores(
    catalog: Annotated[CatalogService, Depends(_get_catalog)],
) -> list[StoreResponse]:
    views = await catalog.list_stores_with_health()
    return [
        StoreResponse(
            id=v.store.id,
            name=v.store.name,
            domain=v.store.domain,
            adapter_key=v.store.adapter_key,
            rate_limit_seconds=v.store.rate_limit_seconds,
            requires_js=v.store.requires_js,
            custom_selectors_json=v.store.custom_selectors_json,
            is_enabled=v.store.is_enabled,
            last_error_summary=v.store.last_error_summary,
            offers_count=v.offers_count,
            active_offers_count=v.active_offers_count,
            failing_offers_count=v.failing_offers_count,
            success_rate_percent=v.success_rate_percent,
        )
        for v in views
    ]


@router.patch("/stores/{store_id}", response_model=StoreResponse)
async def update_store(
    store_id: int,
    payload: StoreUpdateRequest,
    catalog: Annotated[CatalogService, Depends(_get_catalog)],
) -> StoreResponse:
    await catalog.update_store(
        store_id,
        name=payload.name,
        adapter_key=payload.adapter_key,
        rate_limit_seconds=payload.rate_limit_seconds,
        requires_js=payload.requires_js,
        custom_selectors_json=payload.custom_selectors_json,
        clear_custom_selectors=payload.clear_custom_selectors,
        is_enabled=payload.is_enabled,
    )
    views = await catalog.list_stores_with_health()
    matched = next(v for v in views if v.store.id == store_id)
    return StoreResponse(
        id=matched.store.id,
        name=matched.store.name,
        domain=matched.store.domain,
        adapter_key=matched.store.adapter_key,
        rate_limit_seconds=matched.store.rate_limit_seconds,
        requires_js=matched.store.requires_js,
        custom_selectors_json=matched.store.custom_selectors_json,
        is_enabled=matched.store.is_enabled,
        last_error_summary=matched.store.last_error_summary,
        offers_count=matched.offers_count,
        active_offers_count=matched.active_offers_count,
        failing_offers_count=matched.failing_offers_count,
        success_rate_percent=matched.success_rate_percent,
    )


@router.get("/adapters", response_model=list[AdapterInfoResponse])
async def list_adapters(
    engine: Annotated[ScrapingEngine, Depends(_get_engine)],
) -> list[AdapterInfoResponse]:
    return [
        AdapterInfoResponse(
            adapter_key=a.adapter_key,
            display_name=a.display_name,
            supported_domains=list(a.supported_domains),
            prefer_js=a.prefer_js,
        )
        for a in engine.registry.list_adapters()
    ]


@router.get("/jobs", response_model=list[CheckJobResponse])
async def list_jobs(
    catalog: Annotated[CatalogService, Depends(_get_catalog)],
    offer_id: Annotated[int | None, Query()] = None,
    job_status: Annotated[CheckJobStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[CheckJobResponse]:
    jobs = await catalog.list_check_jobs(
        offer_id=offer_id,
        status=job_status.value if job_status is not None else None,
        limit=limit,
    )
    return [
        CheckJobResponse(
            id=j.id,
            offer_id=j.offer_id,
            product_name=j.offer.product.name,
            store_name=j.offer.store.name,
            status=j.status,
            priority=j.priority,
            trigger_type=j.trigger_type,
            scheduled_at=j.scheduled_at,
            started_at=j.started_at,
            finished_at=j.finished_at,
            attempt_number=j.attempt_number,
            http_status=j.http_status,
            duration_ms=j.duration_ms,
            adapter_used=j.adapter_used,
            extraction_layer=j.extraction_layer,
            error_code=j.error_code,
            error_detail=j.error_detail,
        )
        for j in jobs
    ]


@router.post(
    "/offers/{offer_id}/check-now",
    response_model=EnqueueCheckResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_check_now(
    offer_id: int,
    catalog: Annotated[CatalogService, Depends(_get_catalog)],
    scheduler: Annotated[MonitoringScheduler, Depends(_get_scheduler)],
) -> EnqueueCheckResponse:
    # Weryfikacja istnienia oferty (rzuca NotFoundError -> 404)
    await catalog.get_offer(offer_id)
    job_id = await scheduler.enqueue_manual_check(offer_id)
    return EnqueueCheckResponse(job_id=job_id, offer_id=offer_id)


@router.get("/scheduler/status", response_model=SchedulerStatusResponse)
async def get_scheduler_status(
    scheduler: Annotated[MonitoringScheduler, Depends(_get_scheduler)],
) -> SchedulerStatusResponse:
    stats = await scheduler.get_stats()
    return SchedulerStatusResponse(
        is_running=stats.is_running,
        is_paused=stats.is_paused,
        active_workers=stats.active_workers,
        pending_jobs=stats.pending_jobs,
    )


@router.post("/scheduler/pause", response_model=SchedulerStatusResponse)
async def pause_scheduler(
    scheduler: Annotated[MonitoringScheduler, Depends(_get_scheduler)],
) -> SchedulerStatusResponse:
    scheduler.pause()
    stats = await scheduler.get_stats()
    return SchedulerStatusResponse(
        is_running=stats.is_running,
        is_paused=stats.is_paused,
        active_workers=stats.active_workers,
        pending_jobs=stats.pending_jobs,
    )


@router.post("/scheduler/resume", response_model=SchedulerStatusResponse)
async def resume_scheduler(
    scheduler: Annotated[MonitoringScheduler, Depends(_get_scheduler)],
) -> SchedulerStatusResponse:
    scheduler.resume()
    stats = await scheduler.get_stats()
    return SchedulerStatusResponse(
        is_running=stats.is_running,
        is_paused=stats.is_paused,
        active_workers=stats.active_workers,
        pending_jobs=stats.pending_jobs,
    )
