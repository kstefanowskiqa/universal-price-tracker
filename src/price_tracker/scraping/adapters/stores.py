from __future__ import annotations

import json
import re

from selectolax.parser import HTMLParser

from price_tracker.core.enums import AvailabilityStatus, ExtractionLayer
from price_tracker.scraping.adapters.universal import UniversalAdapter, enrich_metadata
from price_tracker.scraping.extractors import extract_with_css_selectors
from price_tracker.scraping.models import ExtractedOffer, StoreScrapeConfig
from price_tracker.scraping.parsers.price_parser import (
    parse_availability,
    parse_price,
)


def _merge_css_fallback(
    primary: ExtractedOffer | None,
    css_offer: ExtractedOffer | None,
    tree: HTMLParser,
) -> ExtractedOffer | None:
    if primary is None:
        return enrich_metadata(css_offer, tree) if css_offer is not None else None
    if css_offer is None:
        return primary

    if (
        primary.regular_price is None
        and css_offer.regular_price is not None
        and (
            primary.current_price is None
            or css_offer.regular_price > primary.current_price
        )
    ):
        primary.regular_price = css_offer.regular_price

    if (
        primary.availability == AvailabilityStatus.UNKNOWN
        and css_offer.availability != AvailabilityStatus.UNKNOWN
    ):
        primary.availability = css_offer.availability

    return primary


class DeclarativeStoreAdapter(UniversalAdapter):
    """Adapter definiowany deklaratywnie zestawem selektorów CSS (np. z pliku JSON/YAML)."""

    def __init__(
        self,
        *,
        adapter_key: str,
        display_name: str,
        supported_domains: tuple[str, ...],
        selectors: dict[str, str],
        default_currency: str | None = None,
        prefer_js: bool = False,
    ) -> None:
        self.adapter_key = adapter_key
        self.display_name = display_name
        self.supported_domains = supported_domains
        self._selectors = selectors
        self.default_currency = default_currency
        self.prefer_js = prefer_js

    def extract_from_html(
        self,
        html: str,
        config: StoreScrapeConfig,
        *,
        from_browser: bool = False,
    ) -> ExtractedOffer | None:
        tree = self.parse_tree(html)
        merged_selectors = {**self._selectors, **(config.custom_selectors or {})}
        default_curr = self.infer_default_currency(config.url) or config.previous_currency

        extracted = extract_with_css_selectors(
            tree,
            merged_selectors,
            default_currency=default_curr,
            layer=ExtractionLayer.PLAYWRIGHT if from_browser else ExtractionLayer.STORE_ADAPTER,
            confidence=0.92,
        )
        if extracted is not None:
            extracted = enrich_metadata(extracted, tree)
            extracted.adapter_key = self.adapter_key
            extracted.store_name = self.display_name
            return extracted

        fallback = super().extract_from_html(html, config, from_browser=from_browser)
        if fallback is not None:
            fallback.adapter_key = self.adapter_key
            fallback.store_name = self.display_name
        return fallback


class XKomAdapter(UniversalAdapter):
    """Dedykowany adapter dla x-kom.pl (obsługa cen promocyjnych, przekreślonych i dostępności)."""

    adapter_key = "xkom"
    display_name = "x-kom"
    supported_domains = ("x-kom.pl",)
    default_currency = "PLN"

    _SELECTORS: dict[str, str] = {
        "title": 'h1[data-name="productTitle"], h1',
        "price": '[data-name="productPrice"], [data-testid="product-price"]',
        "regular_price": '[data-name="productOldPrice"], [data-testid="product-old-price"]',
        "availability": '[data-name="productAvailability"], [data-testid="availability-status"]',
    }

    def extract_from_html(
        self,
        html: str,
        config: StoreScrapeConfig,
        *,
        from_browser: bool = False,
    ) -> ExtractedOffer | None:
        tree = self.parse_tree(html)

        # Najpierw próbujemy warstw strukturalnych (JSON-LD na x-kom bywa najczystszy)
        result = super().extract_from_html(html, config, from_browser=from_browser)

        # Uzupełnienie ceny regularnej (przekreślonej) i dostępności ze specyficznych atrybutów
        css_offer = extract_with_css_selectors(
            tree,
            {**self._SELECTORS, **(config.custom_selectors or {})},
            default_currency="PLN",
            layer=ExtractionLayer.PLAYWRIGHT if from_browser else ExtractionLayer.STORE_ADAPTER,
            confidence=0.94,
        )

        result = _merge_css_fallback(result, css_offer, tree)

        if result is not None:
            result.adapter_key = self.adapter_key
            result.store_name = self.display_name
            result.currency = result.currency or "PLN"
        return result


class HMAdapter(UniversalAdapter):
    """Dedykowany adapter dla H&M (regionalne waluty, ceny wyprzedażowe, dane wariantowe)."""

    adapter_key = "hm"
    display_name = "H&M"
    supported_domains = ("hm.com",)

    _LOCALE_CURRENCY: dict[str, str] = {
        "/pl_pl/": "PLN",
        "/en_us/": "USD",
        "/en_gb/": "GBP",
        "/de_de/": "EUR",
        "/fr_fr/": "EUR",
        "/es_es/": "EUR",
        "/it_it/": "EUR",
        "/cs_cz/": "CZK",
        "/sv_se/": "SEK",
    }

    _SELECTORS: dict[str, str] = {
        "title": "h1",
        "price": '[data-testid="red-price"], [data-testid="white-price"], .price-value',
        "regular_price": '[data-testid="white-price-strikethrough"], .price-original',
        "availability": '[data-testid="availability"], .product-availability',
    }

    _NEXT_DATA_RE = re.compile(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE
    )

    def infer_default_currency(self, url: str) -> str | None:
        lower_url = url.lower()
        for locale_prefix, currency in self._LOCALE_CURRENCY.items():
            if locale_prefix in lower_url:
                return currency
        return None

    def _extract_from_next_data(
        self,
        html: str,
        default_currency: str | None,
    ) -> ExtractedOffer | None:
        match = self._NEXT_DATA_RE.search(html)
        if not match:
            return None
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            return None

        page_props = data.get("props", {}).get("pageProps", {})
        product = page_props.get("product") or page_props.get("productPageProps", {}).get("product")
        if not isinstance(product, dict):
            return None

        sale_raw = product.get("redPrice") or product.get("salePrice")
        white_raw = product.get("whitePrice") or product.get("price")

        primary_raw = sale_raw or white_raw
        parsed = parse_price(
            str(primary_raw.get("value") if isinstance(primary_raw, dict) else primary_raw or ""),
            default_currency=default_currency,
        )
        if parsed is None:
            return None

        current_price, currency = parsed
        regular_price = None
        if sale_raw and white_raw:
            parsed_reg = parse_price(
                str(white_raw.get("value") if isinstance(white_raw, dict) else white_raw),
                default_currency=currency,
            )
            if parsed_reg and parsed_reg[0] > current_price:
                regular_price = parsed_reg[0]

        in_stock = product.get("inStock")
        availability = (
            AvailabilityStatus.IN_STOCK
            if in_stock is True
            else AvailabilityStatus.OUT_OF_STOCK
            if in_stock is False
            else parse_availability(str(product.get("availability") or ""))
        )

        return ExtractedOffer(
            title=str(product.get("name") or "").strip() or None,
            current_price=current_price,
            regular_price=regular_price,
            currency=currency,
            availability=availability,
            store_name=self.display_name,
            adapter_key=self.adapter_key,
            extraction_layer=ExtractionLayer.STORE_ADAPTER,
            confidence=0.95,
        )

    def extract_from_html(
        self,
        html: str,
        config: StoreScrapeConfig,
        *,
        from_browser: bool = False,
    ) -> ExtractedOffer | None:
        default_currency = self.infer_default_currency(config.url) or config.previous_currency
        tree: HTMLParser = self.parse_tree(html)

        result = self._extract_from_next_data(html, default_currency)
        if result is None:
            result = super().extract_from_html(html, config, from_browser=from_browser)
        if result is None:
            result = extract_with_css_selectors(
                tree,
                {**self._SELECTORS, **(config.custom_selectors or {})},
                default_currency=default_currency,
                layer=ExtractionLayer.PLAYWRIGHT if from_browser else ExtractionLayer.STORE_ADAPTER,
                confidence=0.88,
            )

        if result is not None:
            result = enrich_metadata(result, tree)
            result.adapter_key = self.adapter_key
            result.store_name = self.display_name
            if from_browser:
                result.extraction_layer = ExtractionLayer.PLAYWRIGHT
        return result


class VictoriasSecretAdapter(UniversalAdapter):
    """Dedykowany adapter dla Victoria's Secret (obsługa promocji, walut PL/EU/US i wariantów)."""

    adapter_key = "victorias_secret"
    display_name = "Victoria's Secret"
    supported_domains = ("victoriassecret.com",)

    _SELECTORS: dict[str, str] = {
        "title": 'h1[data-testid="product-name"], h1',
        "price": '[data-testid="sale-price"], [data-testid="product-price"], .price-sale',
        "regular_price": '[data-testid="original-price"], .price-original, del',
        "availability": '[data-testid="availability-message"], [data-testid="add-to-bag"]',
    }

    def infer_default_currency(self, url: str) -> str | None:
        lower = url.lower()
        if "pl.victoriassecret.com" in lower or "/pl/" in lower:
            return "PLN"
        if "eu.victoriassecret.com" in lower or "/de/" in lower or "/fr/" in lower:
            return "EUR"
        if "uk.victoriassecret.com" in lower or "/gb/" in lower:
            return "GBP"
        return "USD"

    def extract_from_html(
        self,
        html: str,
        config: StoreScrapeConfig,
        *,
        from_browser: bool = False,
    ) -> ExtractedOffer | None:
        default_currency = self.infer_default_currency(config.url) or config.previous_currency
        tree = self.parse_tree(html)

        result = super().extract_from_html(html, config, from_browser=from_browser)
        css_offer = extract_with_css_selectors(
            tree,
            {**self._SELECTORS, **(config.custom_selectors or {})},
            default_currency=default_currency,
            layer=ExtractionLayer.PLAYWRIGHT if from_browser else ExtractionLayer.STORE_ADAPTER,
            confidence=0.90,
        )

        result = _merge_css_fallback(result, css_offer, tree)

        if result is not None:
            result.adapter_key = self.adapter_key
            result.store_name = self.display_name
            result.currency = result.currency or default_currency
        return result

def _extract_vs_dom_availability(html: str) -> AvailabilityStatus:
    tree = HTMLParser(html)

    # 1. Przycisk "Dodaj do torby / koszyka / Add to Bag"
    cta_selectors = (
        'button[data-testid*="add-to-bag"]',
        'button[data-testid*="addToBag"]',
        'button[id*="add-to-bag"]',
        'button[class*="add-to-bag"]',
        'button[type="submit"]',
        "button",
    )
    in_stock_cta_re = re.compile(
        r"\b(dodaj do torby|dodaj do koszyka|add to bag|add to cart)\b", re.I
    )
    out_of_stock_cta_re = re.compile(
        r"\b(brak w magazynie|wyprzedan[aey]|niedostępn[aey]|"
        r"chwilowo niedostępn|powiadom mnie|notify me|sold out|out of stock)\b",
        re.I,
    )

    for sel in cta_selectors:
        for btn in tree.css(sel):
            text = btn.text(separator=" ", strip=True)
            if not text or len(text) > 60:
                continue
            is_disabled = (
                "disabled" in btn.attributes
                or (btn.attributes.get("aria-disabled") or "").lower() == "true"
            )
            if out_of_stock_cta_re.search(text):
                return AvailabilityStatus.OUT_OF_STOCK
            if in_stock_cta_re.search(text):
                return (
                    AvailabilityStatus.OUT_OF_STOCK
                    if is_disabled
                    else AvailabilityStatus.IN_STOCK
                )

    # 2. Osadzony JSON w skryptach (inStock / outOfStock / stockStatus)
    if re.search(r'"(?:outOfStock|isSoldOut)"\s*:\s*true', html, re.I):
        return AvailabilityStatus.OUT_OF_STOCK
    if re.search(r'"inStock"\s*:\s*false', html, re.I):
        return AvailabilityStatus.OUT_OF_STOCK
    if re.search(r'"inStock"\s*:\s*true', html, re.I):
        return AvailabilityStatus.IN_STOCK
    if re.search(r'"stockStatus"\s*:\s*"(?:OUT_OF_STOCK|SOLD_OUT|UNAVAILABLE)"', html, re.I):
        return AvailabilityStatus.OUT_OF_STOCK
    if re.search(r'"stockStatus"\s*:\s*"(?:IN_STOCK|AVAILABLE)"', html, re.I):
        return AvailabilityStatus.IN_STOCK

    return AvailabilityStatus.UNKNOWN
