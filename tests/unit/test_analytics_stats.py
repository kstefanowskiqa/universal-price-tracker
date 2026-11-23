from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from price_tracker.core.enums import AvailabilityStatus, ExtractionLayer
from price_tracker.services.analytics import PricePointData, compute_price_stats


def _pt(idx: int, hours_offset: int, price: str) -> PricePointData:
    base = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    return PricePointData(
        id=idx,
        recorded_at=base + timedelta(hours=hours_offset),
        price=Decimal(price),
        regular_price=None,
        currency="PLN",
        availability_status=AvailabilityStatus.IN_STOCK,
        source_layer=ExtractionLayer.JSON_LD,
    )


def test_compute_stats_empty_returns_none_fields() -> None:
    stats = compute_price_stats([])
    assert stats.points_count == 0
    assert stats.current_price is None
    assert stats.min_price is None
    assert stats.max_price is None
    assert stats.avg_price is None
    assert stats.last_change_amount is None


def test_compute_stats_single_point_has_no_artificial_change() -> None:
    stats = compute_price_stats([_pt(1, 0, "2500.00")])
    assert stats.points_count == 1
    assert stats.current_price == Decimal("2500.00")
    assert stats.min_price == Decimal("2500.00")
    assert stats.max_price == Decimal("2500.00")
    assert stats.avg_price == Decimal("2500.00")
    assert stats.previous_price is None
    assert stats.last_change_amount is None
    assert stats.period_change_amount is None


def test_compute_stats_detects_min_max_avg_and_last_distinct_change() -> None:
    # Kolejność specjalnie przemieszana, aby sprawdzić sortowanie po czasie
    points = [
        _pt(3, 48, "2000.00"),
        _pt(1, 0, "2500.00"),
        _pt(4, 72, "2000.00"),  # powtórzony pomiar tej samej ceny
        _pt(2, 24, "2700.00"),
    ]
    stats = compute_price_stats(points)

    assert stats.points_count == 4
    assert stats.current_price == Decimal("2000.00")
    assert stats.previous_price == Decimal("2700.00")
    assert stats.min_price == Decimal("2000.00")
    assert stats.max_price == Decimal("2700.00")
    # (2500 + 2700 + 2000 + 2000) / 4 = 2300.00
    assert stats.avg_price == Decimal("2300.00")
    assert stats.last_change_amount == Decimal("-700.00")
    assert stats.last_change_percent == Decimal("-25.93")
    assert stats.period_change_amount == Decimal("-500.00")
    assert stats.period_change_percent == Decimal("-20.00")
