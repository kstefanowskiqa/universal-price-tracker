from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import structlog
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from price_tracker.core.enums import (
    AvailabilityStatus,
    CheckJobStatus,
    CheckTriggerType,
    ErrorCode,
    ExtractionLayer,
)
from price_tracker.db.models import CheckJob, PriceHistory, Store, TrackedOffer
from price_tracker.db.session import DatabaseManager
from price_tracker.logging.setup import get_logger
from price_tracker.scheduler.domain_limiter import DomainRateLimiter
from price_tracker.scraping.diagnostics import is_error_retryable
from price_tracker.scraping.engine import ScrapingEngine
from price_tracker.scraping.models import ScrapeOutcome, StoreScrapeConfig

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PriceCheckEvent:
    """Zdarzenie domenowe emitowane po zakończeniu sprawdzenia (konsumowane w Etapie 4 - alerty)."""

    offer_id: int
    ok: bool
    previous_price: Decimal | None
    current_price: Decimal | None
    currency: str | None
    previous_availability: AvailabilityStatus
    current_availability: AvailabilityStatus
    error_code: ErrorCode
    error_detail: str | None
    checked_at: datetime


EventHook = Callable[[PriceCheckEvent], Awaitable[object]]


@dataclass(slots=True)
class SchedulerStats:
    is_running: bool
    is_paused: bool
    active_workers: int
    pending_jobs: int


class MonitoringScheduler:
    """Bazodanowy harmonogram i kolejka zadań monitorowania cen (odporne na restarty)."""

    def __init__(
        self,
        db_manager: DatabaseManager,
        scraping_engine: ScrapingEngine,
        *,
        rate_limiter: DomainRateLimiter | None = None,
        default_interval_seconds: int = 3600,
        job_retention_days: int = 30,
        on_check_completed: EventHook | None = None,
        poll_interval_seconds: float = 5.0,
        max_retries: int = 3,
        base_retry_delay_seconds: int = 60,
    ) -> None:
        self._db = db_manager
        self._engine = scraping_engine
        self._limiter = rate_limiter or DomainRateLimiter()
        self._default_interval = default_interval_seconds
        self._poll_interval = poll_interval_seconds
        self._max_retries = max_retries
        self._base_retry_delay = base_retry_delay_seconds
        self._on_check_completed = on_check_completed
        self._job_retention_days = max(1, job_retention_days)
        self._last_prune_at: datetime | None = None

        self._running = False
        self._paused = False
        self._stop_event = asyncio.Event()
        self._loop_task: asyncio.Task[None] | None = None
        self._active_tasks: set[asyncio.Task[None]] = set()
        self._inflight_job_ids: set[int] = set()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True
        logger.info("scheduler_paused")

    def resume(self) -> None:
        self._paused = False
        logger.info("scheduler_resumed")

    def set_default_interval(self, seconds: int) -> None:
        self._default_interval = seconds

    async def recover_interrupted_jobs(self) -> int:
        """Oznacza zadania RUNNING przerwane przez restart systemu jako FAILED."""
        now = datetime.now(UTC)
        async with self._db.session() as session:
            stmt = select(CheckJob).where(CheckJob.status == CheckJobStatus.RUNNING)
            stale_jobs = list((await session.execute(stmt)).scalars().all())
            for job in stale_jobs:
                job.status = CheckJobStatus.FAILED
                job.finished_at = now
                job.error_code = ErrorCode.UNEXPECTED_ERROR
                job.error_detail = "Zadanie przerwane przez zamknięcie lub restart aplikacji."
            count = len(stale_jobs)
        if count:
            logger.warning("recovered_interrupted_jobs", count=count)
        return count

    async def start(self) -> None:
        if self._running:
            return
        await self.recover_interrupted_jobs()
        await self.prune_old_jobs()
        self._stop_event.clear()
        self._running = True
        self._loop_task = asyncio.create_task(self._run_loop(), name="monitoring-scheduler-loop")
        logger.info("scheduler_started", poll_interval=self._poll_interval)

    async def stop(self, *, drain_timeout_seconds: float = 10.0) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None

        if self._active_tasks:
            _, pending = await asyncio.wait(self._active_tasks, timeout=drain_timeout_seconds)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        logger.info("scheduler_stopped")

    async def enqueue_manual_check(
        self,
        offer_id: int,
        *,
        trigger: CheckTriggerType = CheckTriggerType.MANUAL,
    ) -> int:
        """Dodaje priorytetowe zadanie natychmiastowego sprawdzenia wskazanej oferty."""
        now = datetime.now(UTC)
        async with self._db.session() as session:
            existing_stmt = select(CheckJob).where(
                CheckJob.offer_id == offer_id,
                CheckJob.status.in_([CheckJobStatus.PENDING, CheckJobStatus.RUNNING]),
            )
            existing = (await session.execute(existing_stmt)).scalar_one_or_none()
            if existing is not None:
                return existing.id

            job = CheckJob(
                offer_id=offer_id,
                status=CheckJobStatus.PENDING,
                priority=1,
                trigger_type=trigger,
                scheduled_at=now,
                attempt_number=1,
                correlation_id=uuid.uuid4().hex[:16],
            )
            session.add(job)
            await session.flush()
            return job.id

    async def enqueue_due_offers(self, *, now: datetime | None = None) -> int:
        """Tworzy zadania CheckJob dla aktywnych ofert, których czas sprawdzenia nadszedł."""
        current_time = now or datetime.now(UTC)
        created = 0

        async with self._db.session() as session:
            due_stmt = (
                select(TrackedOffer)
                .join(Store, TrackedOffer.store_id == Store.id)
                .where(
                    TrackedOffer.is_active.is_(True),
                    Store.is_enabled.is_(True),
                    (TrackedOffer.next_check_at.is_(None))
                    | (TrackedOffer.next_check_at <= current_time),
                )
            )
            due_offers = list((await session.execute(due_stmt)).scalars().all())
            if not due_offers:
                return 0

            active_jobs_stmt = select(CheckJob.offer_id).where(
                CheckJob.status.in_(
                    [
                        CheckJobStatus.PENDING,
                        CheckJobStatus.RUNNING,
                        CheckJobStatus.RETRY_SCHEDULED,
                    ]
                )
            )
            busy_offer_ids = set((await session.execute(active_jobs_stmt)).scalars().all())

            for offer in due_offers:
                if offer.id in busy_offer_ids:
                    continue
                session.add(
                    CheckJob(
                        offer_id=offer.id,
                        status=CheckJobStatus.PENDING,
                        priority=10,
                        trigger_type=CheckTriggerType.SCHEDULED,
                        scheduled_at=current_time,
                        attempt_number=1,
                        correlation_id=uuid.uuid4().hex[:16],
                    )
                )
                created += 1

        return created

    async def run_pending_batch(self, *, limit: int = 20, now: datetime | None = None) -> int:
        """Jednorazowo pobiera i wykonuje gotowe zadania z kolejki (wygodne także dla testów)."""
        current_time = now or datetime.now(UTC)
        async with self._db.session() as session:
            stmt = (
                select(CheckJob.id)
                .where(
                    CheckJob.status.in_([CheckJobStatus.PENDING, CheckJobStatus.RETRY_SCHEDULED]),
                    CheckJob.scheduled_at <= current_time,
                )
                .order_by(CheckJob.priority.asc(), CheckJob.scheduled_at.asc(), CheckJob.id.asc())
                .limit(limit)
            )
            job_ids = [
                jid
                for jid in (await session.execute(stmt)).scalars().all()
                if jid not in self._inflight_job_ids
            ]

        if not job_ids:
            return 0

        tasks: list[asyncio.Task[None]] = []
        for jid in job_ids:
            self._inflight_job_ids.add(jid)
            task = asyncio.create_task(self._execute_job_wrapper(jid), name=f"check-job-{jid}")
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)
            tasks.append(task)

        await asyncio.gather(*tasks, return_exceptions=True)
        return len(job_ids)

    async def get_stats(self) -> SchedulerStats:
        async with self._db.session() as session:
            stmt = select(CheckJob.id).where(
                CheckJob.status.in_([CheckJobStatus.PENDING, CheckJobStatus.RETRY_SCHEDULED])
            )
            pending = len((await session.execute(stmt)).scalars().all())
        return SchedulerStats(
            is_running=self._running,
            is_paused=self._paused,
            active_workers=len(self._active_tasks),
            pending_jobs=pending,
        )

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if not self._paused:
                    await self.enqueue_due_offers()
                    await self._dispatch_ready_jobs()
            except Exception:
                logger.exception("scheduler_tick_error")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval)
            except TimeoutError:
                continue

    async def _dispatch_ready_jobs(self) -> None:
        now = datetime.now(UTC)
        async with self._db.session() as session:
            stmt = (
                select(CheckJob.id)
                .where(
                    CheckJob.status.in_([CheckJobStatus.PENDING, CheckJobStatus.RETRY_SCHEDULED]),
                    CheckJob.scheduled_at <= now,
                )
                .order_by(CheckJob.priority.asc(), CheckJob.scheduled_at.asc(), CheckJob.id.asc())
                .limit(20)
            )
            job_ids = [
                jid
                for jid in (await session.execute(stmt)).scalars().all()
                if jid not in self._inflight_job_ids
            ]

        for jid in job_ids:
            self._inflight_job_ids.add(jid)
            task = asyncio.create_task(self._execute_job_wrapper(jid), name=f"check-job-{jid}")
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)

    async def _execute_job_wrapper(self, job_id: int) -> None:
        try:
            await self.execute_job(job_id)
        except Exception:
            logger.exception("job_execution_unhandled_error", job_id=job_id)
        finally:
            self._inflight_job_ids.discard(job_id)

    async def prune_old_jobs(self, *, now: datetime | None = None) -> int:
        """Usuwa zakończone zadania starsze niż job_retention_days, ograniczając wzrost bazy."""
        current = now or datetime.now(UTC)
        cutoff = current - timedelta(days=self._job_retention_days)
        terminal_statuses = (
            CheckJobStatus.SUCCESS,
            CheckJobStatus.FAILED,
            CheckJobStatus.CANCELLED,
        )
        async with self._db.session() as session:
            stmt = delete(CheckJob).where(
                CheckJob.status.in_(terminal_statuses),
                CheckJob.scheduled_at < cutoff,
            )
            result = await session.execute(stmt)
            deleted = int(getattr(result, "rowcount", 0) or 0)

        self._last_prune_at = current
        now_utc = datetime.now(UTC)
        if self._last_prune_at is None or (now_utc - self._last_prune_at) >= timedelta(hours=6):
            await self.prune_old_jobs(now=now_utc)
        if deleted > 0:
            logger.info("old_check_jobs_pruned", deleted=deleted, cutoff=cutoff.isoformat())
        return deleted

    async def execute_job(self, job_id: int) -> ScrapeOutcome | None:
        """Wykonuje pojedyncze zadanie CheckJob wraz z aktualizacją bazy i ewentualnym retry."""
        started_at = datetime.now(UTC)

        # Krok 1: oznaczenie zadania jako RUNNING i odczyt konfiguracji oferty/sklepu
        async with self._db.session() as session:
            stmt = (
                select(CheckJob)
                .options(selectinload(CheckJob.offer).selectinload(TrackedOffer.store))
                .where(CheckJob.id == job_id)
            )
            job = (await session.execute(stmt)).scalar_one_or_none()
            if job is None or job.status not in {
                CheckJobStatus.PENDING,
                CheckJobStatus.RETRY_SCHEDULED,
            }:
                return None

            offer = job.offer
            store = offer.store
            job.status = CheckJobStatus.RUNNING
            job.started_at = started_at

            scrape_cfg = StoreScrapeConfig(
                url=offer.url,
                domain=store.domain,
                adapter_key=store.adapter_key,
                requires_js=store.requires_js,
                custom_selectors=store.custom_selectors_json,
                variant_label=offer.variant_label,
                variant_selector=offer.variant_selector_json,
                previous_price=offer.current_price,
                previous_currency=offer.currency,
            )
            rate_limit = float(store.rate_limit_seconds)
            correlation_id = job.correlation_id or uuid.uuid4().hex[:16]

        # Krok 2: właściwe pobranie z poszanowaniem limitera domeny (poza transakcją DB!)
        structlog.contextvars.bind_contextvars(
            correlation_id=correlation_id,
            job_id=job_id,
            domain=scrape_cfg.domain,
        )
        try:
            async with self._limiter.acquire(scrape_cfg.domain, min_delay_seconds=rate_limit):
                outcome = await self._engine.scrape(scrape_cfg)
        finally:
            structlog.contextvars.clear_contextvars()

        finished_at = datetime.now(UTC)
        event: PriceCheckEvent | None = None

        # Krok 3: atomowy zapis wyniku w bazie danych
        async with self._db.session() as session:
            stmt = (
                select(CheckJob)
                .options(selectinload(CheckJob.offer).selectinload(TrackedOffer.store))
                .where(CheckJob.id == job_id)
            )
            job = (await session.execute(stmt)).scalar_one()
            offer = job.offer
            store = offer.store

            interval_seconds = offer.check_interval_seconds or self._default_interval
            job.finished_at = finished_at
            job.adapter_used = outcome.adapter_key
            job.extraction_layer = outcome.extraction_layer
            job.http_status = outcome.fetch.status_code if outcome.fetch else None
            job.duration_ms = outcome.fetch.duration_ms if outcome.fetch else None

            if outcome.ok and outcome.offer is not None and outcome.offer.current_price is not None:
                extracted = outcome.offer
                prev_price = offer.current_price
                prev_avail = offer.availability_status
                currency = extracted.currency or offer.currency or "PLN"

                # Rzeczywisty, zweryfikowany pomiar trafia do historii (append-only)
                session.add(
                    PriceHistory(
                        offer_id=offer.id,
                        recorded_at=finished_at,
                        price=extracted.current_price,
                        regular_price=extracted.regular_price,
                        currency=currency,
                        availability_status=extracted.availability,
                        source_layer=extracted.extraction_layer,
                    )
                )

                if prev_price is not None and prev_price != extracted.current_price:
                    offer.previous_price = prev_price
                    offer.last_price_change_at = finished_at

                offer.current_price = extracted.current_price
                offer.regular_price = extracted.regular_price
                offer.currency = currency
                offer.availability_status = extracted.availability
                offer.last_checked_at = finished_at
                offer.next_check_at = finished_at + timedelta(seconds=interval_seconds)
                offer.consecutive_failures = 0
                offer.last_error_code = ErrorCode.NONE
                offer.last_error_message = None

                job.status = CheckJobStatus.SUCCESS
                job.error_code = ErrorCode.NONE
                job.error_detail = None

                event = PriceCheckEvent(
                    offer_id=offer.id,
                    ok=True,
                    previous_price=prev_price,
                    current_price=extracted.current_price,
                    currency=currency,
                    previous_availability=prev_avail,
                    current_availability=extracted.availability,
                    error_code=ErrorCode.NONE,
                    error_detail=None,
                    checked_at=finished_at,
                )
                logger.info(
                    "price_check_succeeded",
                    offer_id=offer.id,
                    price=str(extracted.current_price),
                    currency=currency,
                    layer=extracted.extraction_layer.value,
                )
            else:
                http_status = outcome.fetch.status_code if outcome.fetch else None
                retryable = is_error_retryable(outcome.error_code, http_status)
                should_retry = retryable and job.attempt_number < self._max_retries

                job.error_code = outcome.error_code
                job.error_detail = outcome.error_detail

                if should_retry:
                    backoff_seconds = self._base_retry_delay * (2 ** (job.attempt_number - 1))
                    job.status = CheckJobStatus.FAILED
                    session.add(
                        CheckJob(
                            offer_id=offer.id,
                            status=CheckJobStatus.RETRY_SCHEDULED,
                            priority=job.priority,
                            trigger_type=job.trigger_type,
                            scheduled_at=finished_at + timedelta(seconds=backoff_seconds),
                            attempt_number=job.attempt_number + 1,
                            correlation_id=job.correlation_id,
                        )
                    )
                    logger.warning(
                        "price_check_retry_scheduled",
                        offer_id=offer.id,
                        attempt=job.attempt_number + 1,
                        backoff_seconds=backoff_seconds,
                        error_code=outcome.error_code.value,
                    )
                else:
                    job.status = CheckJobStatus.FAILED
                    offer.consecutive_failures += 1
                    offer.last_checked_at = finished_at
                    offer.next_check_at = finished_at + timedelta(seconds=interval_seconds)
                    offer.last_error_code = outcome.error_code
                    offer.last_error_message = outcome.error_detail
                    store.last_error_summary = (
                        f"[{outcome.error_code.value}] {outcome.error_detail or ''}"
                    ).strip()

                    event = PriceCheckEvent(
                        offer_id=offer.id,
                        ok=False,
                        previous_price=offer.current_price,
                        current_price=offer.current_price,
                        currency=offer.currency,
                        previous_availability=offer.availability_status,
                        current_availability=offer.availability_status,
                        error_code=outcome.error_code,
                        error_detail=outcome.error_detail,
                        checked_at=finished_at,
                    )
                    logger.warning(
                        "price_check_failed",
                        offer_id=offer.id,
                        error_code=outcome.error_code.value,
                        consecutive_failures=offer.consecutive_failures,
                    )

        if event is not None and self._on_check_completed is not None:
            try:
                await self._on_check_completed(event)
            except Exception:
                logger.exception("on_check_completed_hook_failed", offer_id=event.offer_id)

        return outcome


def _unused_layer_hint() -> ExtractionLayer:  # zachowuje spójny import typów
    return ExtractionLayer.JSON_LD
