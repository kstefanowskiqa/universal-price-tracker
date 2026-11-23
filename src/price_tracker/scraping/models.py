from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from price_tracker.core.enums import AvailabilityStatus, ErrorCode, ExtractionLayer


@dataclass(slots=True)
class FetchResult:
    """Wynik pobrania zasobu HTTP lub wyrenderowania strony w Playwright."""

    url: str
    final_url: str
    status_code: int | None
    headers: dict[str, str] = field(default_factory=dict)
    html: str = ""
    duration_ms: int = 0
    used_browser: bool = False
    error_code: ErrorCode = ErrorCode.NONE
    error_detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.error_code == ErrorCode.NONE and bool(self.html)


@dataclass(slots=True)
class ExtractedOffer:
    """Znormalizowane dane produktu pozyskane przez jedną lub kilka warstw ekstrakcji."""

    title: str | None = None
    current_price: Decimal | None = None
    regular_price: Decimal | None = None
    currency: str | None = None
    availability: AvailabilityStatus = AvailabilityStatus.UNKNOWN
    image_url: str | None = None
    store_name: str | None = None
    variant_label: str | None = None
    extraction_layer: ExtractionLayer = ExtractionLayer.JSON_LD
    confidence: float = 0.0
    adapter_key: str = "universal"
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StoreScrapeConfig:
    """Konfiguracja i kontekst historyczny przekazywany do adaptera przy sprawdzeniu oferty."""

    url: str
    domain: str
    adapter_key: str = "universal"
    requires_js: bool = False
    custom_selectors: dict[str, str] | None = None
    variant_label: str = ""
    variant_selector: dict[str, Any] | None = None
    previous_price: Decimal | None = None
    previous_currency: str | None = None


@dataclass(slots=True)
class ScrapeOutcome:
    """Kompletny wynik próby pobrania i analizy ceny wraz z diagnostyką."""

    ok: bool
    offer: ExtractedOffer | None = None
    fetch: FetchResult | None = None
    error_code: ErrorCode = ErrorCode.NONE
    error_detail: str | None = None
    adapter_key: str = "universal"
    extraction_layer: ExtractionLayer | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
