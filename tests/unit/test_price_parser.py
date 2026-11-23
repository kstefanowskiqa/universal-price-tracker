from __future__ import annotations

from decimal import Decimal

import pytest

from price_tracker.core.enums import AvailabilityStatus, ErrorCode
from price_tracker.scraping.parsers.price_parser import (
    normalize_currency,
    parse_availability,
    parse_price,
    validate_price_sanity,
)


@pytest.mark.parametrize(
    ("raw", "default_curr", "expected_price", "expected_curr"),
    [
        ("2 499,99 zł", None, Decimal("2499.99"), "PLN"),
        ("1\u00a0299,00 PLN", None, Decimal("1299.00"), "PLN"),
        ("2499 zł", None, Decimal("2499.00"), "PLN"),
        ("€49.90", None, Decimal("49.90"), "EUR"),
        ("1.299,99 EUR", None, Decimal("1299.99"), "EUR"),
        ("$1,299.99", None, Decimal("1299.99"), "USD"),
        ("£79.95", None, Decimal("79.95"), "GBP"),
        ("199.99", "PLN", Decimal("199.99"), "PLN"),
        ("89,50", "eur", Decimal("89.50"), "EUR"),
    ],
)
def test_parse_price_formats(
    raw: str,
    default_curr: str | None,
    expected_price: Decimal,
    expected_curr: str,
) -> None:
    parsed = parse_price(raw, default_currency=default_curr)
    assert parsed is not None
    assert parsed == (expected_price, expected_curr)


def test_parse_price_rejects_zero_and_invalid() -> None:
    assert parse_price("0,00 zł") is None
    assert parse_price("Brak ceny") is None
    assert parse_price("") is None


def test_normalize_currency() -> None:
    assert normalize_currency("zł") == "PLN"
    assert normalize_currency("€") == "EUR"
    assert normalize_currency("USD") == "USD"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://schema.org/InStock", AvailabilityStatus.IN_STOCK),
        ("https://schema.org/OutOfStock", AvailabilityStatus.OUT_OF_STOCK),
        ("https://schema.org/PreOrder", AvailabilityStatus.PREORDER),
        ("Produkt dostępny w magazynie", AvailabilityStatus.IN_STOCK),
        ("Czasowo niedostępny", AvailabilityStatus.OUT_OF_STOCK),
        ("Sold out", AvailabilityStatus.OUT_OF_STOCK),
        ("Ostatnie sztuki", AvailabilityStatus.LIMITED),
    ],
)
def test_parse_availability(raw: str, expected: AvailabilityStatus) -> None:
    assert parse_availability(raw) == expected


def test_sanity_guard_rules() -> None:
    # Normalna promocja (-25%) -> OK
    code, _ = validate_price_sanity(
        Decimal("1875.00"),
        "PLN",
        previous_price=Decimal("2500.00"),
        previous_currency="PLN",
    )
    assert code == ErrorCode.NONE

    # Zmiana waluty -> odrzucenie
    code, msg = validate_price_sanity(
        Decimal("500.00"),
        "EUR",
        previous_price=Decimal("2500.00"),
        previous_currency="PLN",
    )
    assert code == ErrorCode.CURRENCY_MISMATCH
    assert msg is not None

    # Podejrzany spadek o 95% (np. rata zamiast pełnej ceny) -> odrzucenie
    code, _ = validate_price_sanity(
        Decimal("120.00"),
        "PLN",
        previous_price=Decimal("2500.00"),
        previous_currency="PLN",
    )
    assert code == ErrorCode.ANOMALY_REJECTED

    # Podejrzany wzrost ponad 4x -> odrzucenie
    code, _ = validate_price_sanity(
        Decimal("12000.00"),
        "PLN",
        previous_price=Decimal("2500.00"),
        previous_currency="PLN",
    )
    assert code == ErrorCode.ANOMALY_REJECTED
