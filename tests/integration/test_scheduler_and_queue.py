from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import select

from price_tracker.core.enums import (
    AvailabilityStatus,
    CheckJobStatus,
    ErrorCode,
)
from price_tracker.db.models import CheckJob, PriceHistory, Product, Store, TrackedOffer
from price_tracker.db.session import DatabaseManager
from price_tracker.scheduler.domain_limiter import DomainRateLimiter
from price_tracker.scheduler.worker import MonitoringScheduler, PriceCheckEvent
from price_tracker.scraping.engine import ScrapingEngine
from price_tracker.scraping.http_client import SafeHttpClient


async def _public_dns(_host: str, _port: int) -> list[str]:
    return ["93.184.216.34"]


def _make_product_html(name: str, price: str, currency: str = "PLN") -> str:
    return f"""
    <html><head>
    <script type="application/ld+json">
    {{
      "@context": "https://schema.org",
      "@type": "Product",
      "name": "{name}",
      "offers": {{
        "@type": "Offer",
        "price": "{price}",
        "priceCurrency": "{currency}",
        "availability": "https://schema.org/InStock"
      }}
    }}
    </script>
    </head><body><h1>{name}</h1></body></html>
    """


async def test_scheduler_records_price_changes_and_never_corrupts_on_failure(
    db_manager: DatabaseManager,
) -> None:
    prices_sequence = iter(["2500.00", "2199.00"])
    mode = {"fail_waf": False}

    def handler(_request: httpx.Request) -> httpx.Response:
        if mode["fail_waf"]:
            return httpx.Response(
                403,
                headers={"server": "cloudflare", "cf-mitigated": "challenge"},
                text="<html>Just a moment...</html>",
            )
        return httpx.Response(200, text=_make_product_html("Monitor OLED", next(prices_sequence)))

    http_client = SafeHttpClient(resolver=_public_dns, transport=httpx.MockTransport(handler))
    engine = ScrapingEngine(http_client=http_client, enable_browser_fallback=False)
    limiter = DomainRateLimiter(
        max_global_concurrent=4,
        default_domain_delay_seconds=0.0,
        jitter_ratio=0.0,
    )
    events: list[PriceCheckEvent] = []

    async def on_event(evt: PriceCheckEvent) -> None:
        events.append(evt)

    scheduler = MonitoringScheduler(
        db_manager,
        engine,
        rate_limiter=limiter,
        on_check_completed=on_event,
    )

    try:
        async with db_manager.session() as session:
            store = Store(name="x-kom", domain="x-kom.pl", adapter_key="xkom", rate_limit_seconds=0)
            product = Product(name="Monitor OLED")
            session.add_all([store, product])
            await session.flush()

            offer = TrackedOffer(
                product_id=product.id,
                store_id=store.id,
                url="https://www.x-kom.pl/p/oled-1.html",
                is_active=True,
                check_interval_seconds=600,
                next_check_at=datetime.now(UTC) - timedelta(minutes=1),
            )
            session.add(offer)
            await session.flush()
            offer_id = offer.id

        # 1. Pierwszy pomiar: 2500.00 PLN
        enqueued = await scheduler.enqueue_due_offers()
        assert enqueued == 1
        processed = await scheduler.run_pending_batch()
        assert processed == 1

        # 2. Drugi pomiar (ręczne wymuszenie): spadek na 2199.00 PLN
        await scheduler.enqueue_manual_check(offer_id)
        await scheduler.run_pending_batch()

        # 3. Trzeci pomiar: blokada Cloudflare WAF (nie może nadpisać ceny ani dodać historii!)
        mode["fail_waf"] = True
        await scheduler.enqueue_manual_check(offer_id)
        await scheduler.run_pending_batch()

        async with db_manager.session() as session:
            updated_offer = (
                await session.execute(select(TrackedOffer).where(TrackedOffer.id == offer_id))
            ).scalar_one()
            history = list(
                (
                    await session.execute(
                        select(PriceHistory)
                        .where(PriceHistory.offer_id == offer_id)
                        .order_by(PriceHistory.id.asc())
                    )
                )
                .scalars()
                .all()
            )

        assert len(history) == 2
        assert history[0].price == Decimal("2500.00")
        assert history[1].price == Decimal("2199.00")
        assert updated_offer.current_price == Decimal("2199.00")
        assert updated_offer.previous_price == Decimal("2500.00")
        assert updated_offer.last_price_change_at is not None
        assert updated_offer.consecutive_failures == 1
        assert updated_offer.last_error_code == ErrorCode.BLOCKED_WAF
        assert updated_offer.availability_status == AvailabilityStatus.IN_STOCK
        assert len(events) == 3
        assert events[1].previous_price == Decimal("2500.00")
        assert events[1].current_price == Decimal("2199.00")
        assert events[2].ok is False
    finally:
        await engine.close()


async def test_scheduler_schedules_retry_with_backoff_on_timeout(
    db_manager: DatabaseManager,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    http_client = SafeHttpClient(resolver=_public_dns, transport=httpx.MockTransport(handler))
    engine = ScrapingEngine(http_client=http_client, enable_browser_fallback=False)
    limiter = DomainRateLimiter(default_domain_delay_seconds=0.0, jitter_ratio=0.0)
    scheduler = MonitoringScheduler(
        db_manager,
        engine,
        rate_limiter=limiter,
        max_retries=3,
        base_retry_delay_seconds=30,
    )

    try:
        async with db_manager.session() as session:
            store = Store(name="H&M", domain="hm.com", adapter_key="hm", rate_limit_seconds=0)
            product = Product(name="Sweter")
            session.add_all([store, product])
            await session.flush()
            offer = TrackedOffer(
                product_id=product.id,
                store_id=store.id,
                url="https://www2.hm.com/pl_pl/productpage.1.html",
            )
            session.add(offer)
            await session.flush()
            offer_id = offer.id

        await scheduler.enqueue_manual_check(offer_id)
        await scheduler.run_pending_batch()

        async with db_manager.session() as session:
            jobs = list(
                (
                    await session.execute(
                        select(CheckJob)
                        .where(CheckJob.offer_id == offer_id)
                        .order_by(CheckJob.id.asc())
                    )
                )
                .scalars()
                .all()
            )
            history_count = len((await session.execute(select(PriceHistory))).scalars().all())

        assert history_count == 0
        assert len(jobs) == 2
        assert jobs[0].status == CheckJobStatus.FAILED
        assert jobs[0].error_code == ErrorCode.TIMEOUT
        assert jobs[1].status == CheckJobStatus.RETRY_SCHEDULED
        assert jobs[1].attempt_number == 2
        assert jobs[1].scheduled_at > jobs[0].finished_at  # type: ignore[operator]
    finally:
        await engine.close()


async def test_scheduler_recovers_interrupted_jobs_after_restart(
    db_manager: DatabaseManager,
) -> None:
    http_client = SafeHttpClient(
        resolver=_public_dns,
        transport=httpx.MockTransport(lambda _req: httpx.Response(200, text="")),
    )
    engine = ScrapingEngine(http_client=http_client, enable_browser_fallback=False)
    scheduler = MonitoringScheduler(db_manager, engine)

    try:
        async with db_manager.session() as session:
            store = Store(name="Test", domain="test.pl")
            product = Product(name="Produkt")
            session.add_all([store, product])
            await session.flush()
            offer = TrackedOffer(
                product_id=product.id,
                store_id=store.id,
                url="https://test.pl/p/1",
            )
            session.add(offer)
            await session.flush()

            session.add(
                CheckJob(
                    offer_id=offer.id,
                    status=CheckJobStatus.RUNNING,
                    scheduled_at=datetime.now(UTC) - timedelta(minutes=5),
                    started_at=datetime.now(UTC) - timedelta(minutes=4),
                )
            )

        recovered = await scheduler.recover_interrupted_jobs()
        assert recovered == 1

        async with db_manager.session() as session:
            job = (await session.execute(select(CheckJob))).scalar_one()
            assert job.status == CheckJobStatus.FAILED
            assert job.error_code == ErrorCode.UNEXPECTED_ERROR
    finally:
        await engine.close()

async def test_scheduler_prunes_old_terminal_jobs_only(db_manager: DatabaseManager) -> None:
    async with db_manager.session() as session:
        store = Store(name="x-kom", domain="x-kom.pl", adapter_key="xkom", rate_limit_seconds=0)
        product = Product(name="Dysk SSD")
        session.add_all([store, product])
        await session.flush()

        offer = TrackedOffer(
            product_id=product.id,
            store_id=store.id,
            url="https://www.x-kom.pl/p/ssd-1tb.html",
        )
        session.add(offer)
        await session.flush()

        now = datetime.now(UTC)
        old_ts = now - timedelta(days=45)
        recent_ts = now - timedelta(days=2)

        session.add_all(
            [
                CheckJob(
                    offer_id=offer.id,
                    status=CheckJobStatus.SUCCESS,
                    scheduled_at=old_ts,
                ),
                CheckJob(
                    offer_id=offer.id,
                    status=CheckJobStatus.PENDING,
                    scheduled_at=old_ts,
                ),
                CheckJob(
                    offer_id=offer.id,
                    status=CheckJobStatus.SUCCESS,
                    scheduled_at=recent_ts,
                ),
            ]
        )

    engine = ScrapingEngine(enable_browser_fallback=False)
    scheduler = MonitoringScheduler(db_manager, engine, job_retention_days=30)
    try:
        deleted = await scheduler.prune_old_jobs(now=now)
        assert deleted == 1

        async with db_manager.session() as session:
            remaining = list((await session.execute(select(CheckJob))).scalars().all())
            assert len(remaining) == 2
            statuses = {j.status for j in remaining}
            assert statuses == {CheckJobStatus.PENDING, CheckJobStatus.SUCCESS}
    finally:
        await engine.close()
