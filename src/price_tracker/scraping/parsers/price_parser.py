from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from price_tracker.core.enums import AvailabilityStatus, ErrorCode

CURRENCY_SYMBOL_MAP: dict[str, str] = {
    "zł": "PLN",
    "zl": "PLN",
    "pln": "PLN",
    "€": "EUR",
    "eur": "EUR",
    "$": "USD",
    "us$": "USD",
    "usd": "USD",
    "£": "GBP",
    "gbp": "GBP",
    "chf": "CHF",
    "kč": "CZK",
    "czk": "CZK",
    "kr": "SEK",
    "sek": "SEK",
    "nok": "NOK",
    "dkk": "DKK",
}

_CURRENCY_TOKEN_RE = re.compile(
    r"(?i)(?<![A-Za-z])(PLN|EUR|USD|GBP|CHF|CZK|SEK|NOK|DKK|US\$|zł|zl|kč|kr)(?![A-Za-z])|[€$£]"
)
_NUMBER_RE = re.compile(r"\d[\d\s\u00a0\u202f'.,]*\d|\d+")


def normalize_currency(raw: str | None) -> str | None:
    """Mapuje symbol lub kod waluty na 3-literowy standard ISO 4217."""
    if not raw:
        return None
    cleaned = raw.strip().lower()
    if cleaned in CURRENCY_SYMBOL_MAP:
        return CURRENCY_SYMBOL_MAP[cleaned]
    if len(cleaned) == 3 and cleaned.isalpha():
        return cleaned.upper()
    match = _CURRENCY_TOKEN_RE.search(raw)
    if match:
        token = match.group(0).strip().lower()
        return CURRENCY_SYMBOL_MAP.get(token, token.upper() if len(token) == 3 else None)
    return None


def _parse_numeric_string(num_str: str) -> Decimal | None:
    """Konwertuje napis liczbowy w formacie PL/EU/US na Decimal bez użycia float."""
    cleaned = re.sub(r"[\s\u00a0\u202f']", "", num_str.strip())
    if not cleaned:
        return None

    has_dot = "." in cleaned
    has_comma = "," in cleaned

    if has_dot and has_comma:
        # Ostatni separator to separator dziesiętny (np. 1.299,99 lub 1,299.99)
        if cleaned.rfind(",") > cleaned.rfind("."):
            normalized = cleaned.replace(".", "").replace(",", ".")
        else:
            normalized = cleaned.replace(",", "")
    elif has_comma:
        parts = cleaned.split(",")
        if len(parts) == 2 and len(parts[1]) in {1, 2}:
            normalized = f"{parts[0]}.{parts[1]}"
        elif len(parts) >= 2 and all(len(p) == 3 for p in parts[1:]):
            # Separator tysięcy (np. 1,299)
            normalized = "".join(parts)
        else:
            normalized = cleaned.replace(",", ".")
    elif has_dot:
        parts = cleaned.split(".")
        if len(parts) == 2 and len(parts[1]) in {1, 2}:
            normalized = cleaned
        elif len(parts) >= 2 and all(len(p) == 3 for p in parts[1:]):
            # Separator tysięcy w formacie EU (np. 1.299)
            normalized = "".join(parts)
        else:
            normalized = cleaned
    else:
        normalized = cleaned

    try:
        value = Decimal(normalized)
    except InvalidOperation:
        return None

    if value <= 0:
        return None
    return value.quantize(Decimal("0.01"))


def parse_price(
    raw: str | int | Decimal | None,
    *,
    default_currency: str | None = None,
) -> tuple[Decimal, str | None] | None:
    """Parsuje surowy zapis ceny i zwraca krotkę (Decimal, waluta ISO)."""
    if raw is None:
        return None

    if isinstance(raw, Decimal):
        if raw <= 0:
            return None
        return raw.quantize(Decimal("0.01")), normalize_currency(default_currency)

    if isinstance(raw, int) and not isinstance(raw, bool):
        if raw <= 0:
            return None
        return Decimal(raw).quantize(Decimal("0.01")), normalize_currency(default_currency)

    text = str(raw).strip()
    if not text:
        return None

    detected_currency = normalize_currency(text) or normalize_currency(default_currency)
    match = _NUMBER_RE.search(text)
    if not match:
        return None

    value = _parse_numeric_string(match.group(0))
    if value is None:
        return None

    return value, detected_currency


def parse_availability(raw: str | None) -> AvailabilityStatus:
    """Normalizuje status dostępności ze Schema.org lub tekstu ze strony sklepu."""
    if not raw:
        return AvailabilityStatus.UNKNOWN

    text = raw.strip().lower()
    if not text:
        return AvailabilityStatus.UNKNOWN

    # Schema.org URL lub wartości enumeracyjne
    if "outofstock" in text or "soldout" in text or "discontinued" in text:
        return AvailabilityStatus.OUT_OF_STOCK
    if "preorder" in text or "presale" in text or "backorder" in text:
        return AvailabilityStatus.PREORDER
    if "limitedavailability" in text:
        return AvailabilityStatus.LIMITED
    if "instock" in text or "onlineonly" in text:
        return AvailabilityStatus.IN_STOCK

    # Frazy tekstowe (PL / EN)
    out_phrases = (
        "niedostępny",
        "niedostepny",
        "wyprzedane",
        "wyprzedany",
        "brak w magazynie",
        "brak towaru",
        "produkt wycofany",
        "out of stock",
        "sold out",
        "currently unavailable",
        "temporarily unavailable",
        "chwilowo niedostępny",
        "czasowo niedostępny",
    )
    if any(p in text for p in out_phrases):
        return AvailabilityStatus.OUT_OF_STOCK

    preorder_phrases = ("przedsprzedaż", "przedsprzedaz", "pre-order", "preorder")
    if any(p in text for p in preorder_phrases):
        return AvailabilityStatus.PREORDER

    limited_phrases = ("ostatnie sztuki", "mała ilość", "low stock", "only a few left")
    if any(p in text for p in limited_phrases):
        return AvailabilityStatus.LIMITED

    in_phrases = (
        "dostępny",
        "dostepny",
        "w magazynie",
        "in stock",
        "dodaj do koszyka",
        "add to bag",
        "add to cart",
        "kup teraz",
    )
    if any(p in text for p in in_phrases):
        return AvailabilityStatus.IN_STOCK

    return AvailabilityStatus.UNKNOWN


def validate_price_sanity(
    new_price: Decimal,
    new_currency: str | None,
    *,
    previous_price: Decimal | None,
    previous_currency: str | None,
    max_drop_ratio: Decimal = Decimal("0.80"),
    max_rise_ratio: Decimal = Decimal("4.00"),
) -> tuple[ErrorCode, str | None]:
    """Chroni historię cen przed błędną walutą lub skrajnymi anomaliami parsera (np. rata 0%)."""
    if new_price <= 0:
        return ErrorCode.ANOMALY_REJECTED, f"Odrzucono nieprawidłową cenę <= 0 ({new_price})."

    if previous_currency and new_currency and previous_currency.upper() != new_currency.upper():
        return (
            ErrorCode.CURRENCY_MISMATCH,
            f"Wykryto zmianę waluty oferty z {previous_currency} na {new_currency}. "
            "Pomiar odrzucony, aby nie mieszać walut w historii.",
        )

    if previous_price is not None and previous_price > 0:
        drop_ratio = (previous_price - new_price) / previous_price
        if drop_ratio > max_drop_ratio:
            pct = (drop_ratio * Decimal("100")).quantize(Decimal("0.1"))
            return (
                ErrorCode.ANOMALY_REJECTED,
                f"Podejrzany spadek ceny o {pct}% ({previous_price} -> {new_price}). "
                "Możliwe błędne pobranie ceny raty lub akcesorium.",
            )

        rise_ratio = new_price / previous_price
        if rise_ratio > max_rise_ratio:
            return (
                ErrorCode.ANOMALY_REJECTED,
                f"Podejrzany wzrost ceny ponad {max_rise_ratio}x "
                f"({previous_price} -> {new_price}).",
            )

    return ErrorCode.NONE, None
