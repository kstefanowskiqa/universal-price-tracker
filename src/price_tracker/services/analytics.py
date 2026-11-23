from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from price_tracker.core.enums import AvailabilityStatus, ErrorCode, ExtractionLayer
from price_tracker.core.exceptions import NotFoundError
from price_tracker.db.models import PriceHistory, Product, TrackedOffer
from price_tracker.db.session import DatabaseManager

TWO_PLACES = Decimal("0.01")


class TimeRange(StrEnum):
    H24 = "24h"
    D7 = "7d"
    D30 = "30d"
    D90 = "90d"
    D180 = "180d"
    ALL = "all"

    def cutoff(self, now: datetime) -> datetime | None:
        mapping: dict[TimeRange, timedelta] = {
            TimeRange.H24: timedelta(hours=24),
            TimeRange.D7: timedelta(days=7),
            TimeRange.D30: timedelta(days=30),
            TimeRange.D90: timedelta(days=90),
            TimeRange.D180: timedelta(days=180),
        }
        delta = mapping.get(self)
        return now - delta if delta is not None else None


@dataclass(frozen=True, slots=True)
class PricePointData:
    id: int
    recorded_at: datetime
    price: Decimal
    regular_price: Decimal | None
    currency: str
    availability_status: AvailabilityStatus
    source_layer: ExtractionLayer


@dataclass(frozen=True, slots=True)
class PriceStats:
    points_count: int
    currency: str | None
    current_price: Decimal | None
    previous_price: Decimal | None
    min_price: Decimal | None
    max_price: Decimal | None
    avg_price: Decimal | None
    last_change_amount: Decimal | None
    last_change_percent: Decimal | None
    period_change_amount: Decimal | None
    period_change_percent: Decimal | None
    lowest_recorded_at: datetime | None
    highest_recorded_at: datetime | None
    first_recorded_at: datetime | None
    last_recorded_at: datetime | None


@dataclass(frozen=True, slots=True)
class OfferHistoryData:
    offer_id: int
    product_id: int
    product_name: str
    store_name: str
    store_domain: str
    variant_label: str
    url: str
    time_range: TimeRange
    stats: PriceStats
    points: list[PricePointData]


@dataclass(frozen=True, slots=True)
class ProductComparisonData:
    product_id: int
    product_name: str
    time_range: TimeRange
    lowest_current_offer_id: int | None
    offers: list[OfferHistoryData]


@dataclass(frozen=True, slots=True)
class RecentPriceChange:
    offer_id: int
    product_id: int
    product_name: str
    store_name: str
    variant_label: str
    url: str
    previous_price: Decimal
    current_price: Decimal
    currency: str
    change_amount: Decimal
    change_percent: Decimal
    changed_at: datetime


@dataclass(frozen=True, slots=True)
class OfferAttentionItem:
    offer_id: int
    product_id: int
    product_name: str
    store_name: str
    variant_label: str
    availability_status: AvailabilityStatus
    consecutive_failures: int
    last_error_code: ErrorCode
    last_error_message: str | None
    last_checked_at: datetime | None


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    total_products: int
    total_offers: int
    active_offers: int
    paused_offers: int
    offers_with_errors: int
    out_of_stock_offers: int
    price_drops_24h: int
    price_rises_24h: int
    recent_changes: list[RecentPriceChange]
    attention_items: list[OfferAttentionItem]


def _percent_change(old_price: Decimal, new_price: Decimal) -> Decimal | None:
    if old_price <= 0:
        return None
    pct = ((new_price - old_price) / old_price) * Decimal("100")
    return pct.quantize(TWO_PLACES)


def compute_price_stats(points: list[PricePointData]) -> PriceStats:
    """Oblicza statystyki cenowe wyłącznie z rzeczywistych pomiarów (posortowanych rosnąco)."""
    if not points:
        return PriceStats(
            points_count=0,
            currency=None,
            current_price=None,
            previous_price=None,
            min_price=None,
            max_price=None,
            avg_price=None,
            last_change_amount=None,
            last_change_percent=None,
            period_change_amount=None,
            period_change_percent=None,
            lowest_recorded_at=None,
            highest_recorded_at=None,
            first_recorded_at=None,
            last_recorded_at=None,
        )

    ordered = sorted(points, key=lambda p: (p.recorded_at, p.id))
    first_pt = ordered[0]
    last_pt = ordered[-1]

    min_pt = min(ordered, key=lambda p: (p.price, p.recorded_at))
    max_pt = max(ordered, key=lambda p: (p.price, p.recorded_at))

    total = sum((p.price for p in ordered), start=Decimal("0"))
    avg_price = (total / Decimal(len(ordered))).quantize(TWO_PLACES)

    # Ostatnia rzeczywista zmiana ceny (szukamy ostatniego pomiaru o innej cenie niż bieżąca)
    previous_distinct: Decimal | None = None
    for pt in reversed(ordered[:-1]):
        if pt.price != last_pt.price:
            previous_distinct = pt.price
            break

    last_change_amount: Decimal | None = None
    last_change_percent: Decimal | None = None
    if previous_distinct is not None:
        last_change_amount = (last_pt.price - previous_distinct).quantize(TWO_PLACES)
        last_change_percent = _percent_change(previous_distinct, last_pt.price)

    period_change_amount: Decimal | None = None
    period_change_percent: Decimal | None = None
    if len(ordered) >= 2:
        period_change_amount = (last_pt.price - first_pt.price).quantize(TWO_PLACES)
        period_change_percent = _percent_change(first_pt.price, last_pt.price)

    return PriceStats(
        points_count=len(ordered),
        currency=last_pt.currency,
        current_price=last_pt.price.quantize(TWO_PLACES),
        previous_price=previous_distinct.quantize(TWO_PLACES) if previous_distinct else None,
        min_price=min_pt.price.quantize(TWO_PLACES),
        max_price=max_pt.price.quantize(TWO_PLACES),
        avg_price=avg_price,
        last_change_amount=last_change_amount,
        last_change_percent=last_change_percent,
        period_change_amount=period_change_amount,
        period_change_percent=period_change_percent,
        lowest_recorded_at=min_pt.recorded_at,
        highest_recorded_at=max_pt.recorded_at,
        first_recorded_at=first_pt.recorded_at,
        last_recorded_at=last_pt.recorded_at,
    )


class AnalyticsService:
    """Dostarcza historię cen, statystyki okresowe, porównanie sklepów i podsumowanie Dashboardu."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self._db = db_manager

    async def get_offer_history(
        self,
        offer_id: int,
        time_range: TimeRange = TimeRange.D30,
        *,
        now: datetime | None = None,
    ) -> OfferHistoryData:
        current_time = now or datetime.now(UTC)
        cutoff = time_range.cutoff(current_time)

        async with self._db.session() as session:
            offer_stmt = (
                select(TrackedOffer)
                .options(
                    selectinload(TrackedOffer.product),
                    selectinload(TrackedOffer.store),
                )
                .where(TrackedOffer.id == offer_id)
            )
            offer = (await session.execute(offer_stmt)).scalar_one_or_none()
            if offer is None:
                raise NotFoundError(f"Oferta o id={offer_id} nie istnieje.")

            history_stmt = select(PriceHistory).where(PriceHistory.offer_id == offer_id)
            if cutoff is not None:
                history_stmt = history_stmt.where(PriceHistory.recorded_at >= cutoff)
            history_stmt = history_stmt.order_by(
                PriceHistory.recorded_at.asc(), PriceHistory.id.asc()
            )
            rows = list((await session.execute(history_stmt)).scalars().all())

        points = [
            PricePointData(
                id=row.id,
                recorded_at=row.recorded_at,
                price=row.price.quantize(TWO_PLACES),
                regular_price=row.regular_price.quantize(TWO_PLACES)
                if row.regular_price is not None
                else None,
                currency=row.currency,
                availability_status=row.availability_status,
                source_layer=row.source_layer,
            )
            for row in rows
        ]
        stats = compute_price_stats(points)

        return OfferHistoryData(
            offer_id=offer.id,
            product_id=offer.product_id,
            product_name=offer.product.name,
            store_name=offer.store.name,
            store_domain=offer.store.domain,
            variant_label=offer.variant_label,
            url=offer.url,
            time_range=time_range,
            stats=stats,
            points=points,
        )

    async def get_product_comparison(
        self,
        product_id: int,
        time_range: TimeRange = TimeRange.D30,
        *,
        now: datetime | None = None,
    ) -> ProductComparisonData:
        current_time = now or datetime.now(UTC)
        cutoff = time_range.cutoff(current_time)

        async with self._db.session() as session:
            product_stmt = (
                select(Product)
                .options(selectinload(Product.offers).selectinload(TrackedOffer.store))
                .where(Product.id == product_id)
            )
            product = (await session.execute(product_stmt)).scalar_one_or_none()
            if product is None:
                raise NotFoundError(f"Produkt o id={product_id} nie istnieje.")

            offer_ids = [o.id for o in product.offers]
            history_by_offer: dict[int, list[PricePointData]] = {oid: [] for oid in offer_ids}

            if offer_ids:
                hist_stmt = select(PriceHistory).where(PriceHistory.offer_id.in_(offer_ids))
                if cutoff is not None:
                    hist_stmt = hist_stmt.where(PriceHistory.recorded_at >= cutoff)
                hist_stmt = hist_stmt.order_by(
                    PriceHistory.recorded_at.asc(), PriceHistory.id.asc()
                )
                for row in (await session.execute(hist_stmt)).scalars().all():
                    history_by_offer[row.offer_id].append(
                        PricePointData(
                            id=row.id,
                            recorded_at=row.recorded_at,
                            price=row.price.quantize(TWO_PLACES),
                            regular_price=row.regular_price.quantize(TWO_PLACES)
                            if row.regular_price is not None
                            else None,
                            currency=row.currency,
                            availability_status=row.availability_status,
                            source_layer=row.source_layer,
                        )
                    )

        series: list[OfferHistoryData] = []
        best_offer_id: int | None = None
        best_price: Decimal | None = None

        for offer in sorted(product.offers, key=lambda o: o.id):
            pts = history_by_offer.get(offer.id, [])
            stats = compute_price_stats(pts)
            series.append(
                OfferHistoryData(
                    offer_id=offer.id,
                    product_id=product.id,
                    product_name=product.name,
                    store_name=offer.store.name,
                    store_domain=offer.store.domain,
                    variant_label=offer.variant_label,
                    url=offer.url,
                    time_range=time_range,
                    stats=stats,
                    points=pts,
                )
            )
            if (
                offer.current_price is not None
                and offer.availability_status != AvailabilityStatus.OUT_OF_STOCK
                and (best_price is None or offer.current_price < best_price)
            ):
                best_price = offer.current_price
                best_offer_id = offer.id

        return ProductComparisonData(
            product_id=product.id,
            product_name=product.name,
            time_range=time_range,
            lowest_current_offer_id=best_offer_id,
            offers=series,
        )

    async def get_dashboard_summary(
        self,
        *,
        recent_changes_limit: int = 15,
        attention_limit: int = 15,
        now: datetime | None = None,
    ) -> DashboardSummary:
        current_time = now or datetime.now(UTC)
        since_24h = current_time - timedelta(hours=24)

        async with self._db.session() as session:
            total_products = int(
                (await session.execute(select(func.count(Product.id)))).scalar_one()
            )
            offers = list(
                (
                    await session.execute(
                        select(TrackedOffer).options(
                            selectinload(TrackedOffer.product),
                            selectinload(TrackedOffer.store),
                        )
                    )
                )
                .scalars()
                .all()
            )

        total_offers = len(offers)
        active_offers = sum(1 for o in offers if o.is_active)
        paused_offers = total_offers - active_offers
        offers_with_errors = sum(1 for o in offers if o.consecutive_failures > 0)
        out_of_stock_offers = sum(
            1 for o in offers if o.availability_status == AvailabilityStatus.OUT_OF_STOCK
        )

        price_drops_24h = 0
        price_rises_24h = 0
        changed_offers: list[TrackedOffer] = []
        attention_candidates: list[TrackedOffer] = []

        for offer in offers:
            if (
                offer.current_price is not None
                and offer.previous_price is not None
                and offer.last_price_change_at is not None
            ):
                changed_offers.append(offer)
                if offer.last_price_change_at >= since_24h:
                    if offer.current_price < offer.previous_price:
                        price_drops_24h += 1
                    elif offer.current_price > offer.previous_price:
                        price_rises_24h += 1

            if (
                offer.consecutive_failures > 0
                or offer.availability_status == AvailabilityStatus.OUT_OF_STOCK
            ):
                attention_candidates.append(offer)

        changed_offers.sort(
            key=lambda o: o.last_price_change_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        recent_changes: list[RecentPriceChange] = []
        for offer in changed_offers[:recent_changes_limit]:
            assert offer.previous_price is not None
            assert offer.current_price is not None
            assert offer.last_price_change_at is not None
            diff = (offer.current_price - offer.previous_price).quantize(TWO_PLACES)
            pct = _percent_change(offer.previous_price, offer.current_price) or Decimal("0.00")
            recent_changes.append(
                RecentPriceChange(
                    offer_id=offer.id,
                    product_id=offer.product_id,
                    product_name=offer.product.name,
                    store_name=offer.store.name,
                    variant_label=offer.variant_label,
                    url=offer.url,
                    previous_price=offer.previous_price.quantize(TWO_PLACES),
                    current_price=offer.current_price.quantize(TWO_PLACES),
                    currency=offer.currency or "PLN",
                    change_amount=diff,
                    change_percent=pct,
                    changed_at=offer.last_price_change_at,
                )
            )

        min_utc = datetime.min.replace(tzinfo=UTC)
        attention_candidates.sort(
            key=lambda o: (o.consecutive_failures, o.last_checked_at or min_utc),
            reverse=True,
        )
        attention_items = [
            OfferAttentionItem(
                offer_id=o.id,
                product_id=o.product_id,
                product_name=o.product.name,
                store_name=o.store.name,
                variant_label=o.variant_label,
                availability_status=o.availability_status,
                consecutive_failures=o.consecutive_failures,
                last_error_code=o.last_error_code,
                last_error_message=o.last_error_message,
                last_checked_at=o.last_checked_at,
            )
            for o in attention_candidates[:attention_limit]
        ]

        return DashboardSummary(
            total_products=total_products,
            total_offers=total_offers,
            active_offers=active_offers,
            paused_offers=paused_offers,
            offers_with_errors=offers_with_errors,
            out_of_stock_offers=out_of_stock_offers,
            price_drops_24h=price_drops_24h,
            price_rises_24h=price_rises_24h,
            recent_changes=recent_changes,
            attention_items=attention_items,
        )
