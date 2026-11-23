from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from selectolax.parser import HTMLParser

from price_tracker.core.enums import AvailabilityStatus, ExtractionLayer
from price_tracker.scraping.models import ExtractedOffer
from price_tracker.scraping.parsers.price_parser import (
    normalize_currency,
    parse_availability,
    parse_price,
)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _has_type(node: dict[str, Any], target_types: set[str]) -> bool:
    raw_type = node.get("@type")
    for item in _as_list(raw_type):
        if isinstance(item, str):
            short = item.rsplit("/", 1)[-1].lower()
            if short in target_types:
                return True
    return False


def _collect_product_nodes(data: Any) -> list[dict[str, Any]]:
    """Rekurencyjnie wyszukuje węzły Schema.org Product / ProductGroup (w tym w @graph)."""
    products: list[dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            products.extend(_collect_product_nodes(item))
    elif isinstance(data, dict):
        if _has_type(data, {"product", "productgroup"}):
            products.append(data)
            for variant in _as_list(data.get("hasVariant")):
                if isinstance(variant, dict):
                    products.append(variant)
        if "@graph" in data:
            products.extend(_collect_product_nodes(data["@graph"]))
        if "mainEntity" in data:
            products.extend(_collect_product_nodes(data["mainEntity"]))
    return products


def _extract_image(node: dict[str, Any]) -> str | None:
    raw = node.get("image")
    for item in _as_list(raw):
        if isinstance(item, str) and item.startswith(("http://", "https://")):
            return item
        if isinstance(item, dict):
            url = item.get("url") or item.get("contentUrl")
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                return url
    return None


def _extract_price_from_offer(
    offer: dict[str, Any],
    default_currency: str | None,
) -> tuple[Decimal | None, Decimal | None, str | None, AvailabilityStatus]:
    currency = normalize_currency(str(offer.get("priceCurrency") or "")) or default_currency
    availability = parse_availability(str(offer.get("availability") or ""))

    current_price: Decimal | None = None
    regular_price: Decimal | None = None

    raw_price = offer.get("price") or offer.get("lowPrice")
    if raw_price is not None:
        parsed = parse_price(str(raw_price), default_currency=currency)
        if parsed:
            current_price, parsed_curr = parsed
            currency = currency or parsed_curr

    # Cena regularna / przekreślona w priceSpecification (np. Schema.org ListPrice / Strikethrough)
    for spec in _as_list(offer.get("priceSpecification")):
        if not isinstance(spec, dict):
            continue
        spec_curr = normalize_currency(str(spec.get("priceCurrency") or "")) or currency
        spec_parsed = parse_price(str(spec.get("price") or ""), default_currency=spec_curr)
        if not spec_parsed:
            continue
        spec_val, parsed_curr = spec_parsed
        currency = currency or parsed_curr
        price_type = str(spec.get("priceType") or "").lower()
        if "listprice" in price_type or "strikethrough" in price_type:
            regular_price = spec_val
        elif current_price is None:
            current_price = spec_val

    high_raw = offer.get("highPrice")
    if high_raw is not None and current_price is not None and regular_price is None:
        parsed_high = parse_price(str(high_raw), default_currency=currency)
        if parsed_high and parsed_high[0] > current_price:
            regular_price = parsed_high[0]

    return current_price, regular_price, currency, availability


def _pick_best_offer(
    offers: list[dict[str, Any]],
    variant_label: str,
) -> dict[str, Any] | None:
    if not offers:
        return None
    if variant_label:
        needle = variant_label.strip().lower()
        for offer in offers:
            haystack = " ".join(
                str(offer.get(k) or "") for k in ("sku", "name", "url", "itemOffered")
            ).lower()
            if needle in haystack:
                return offer
    # Preferujemy ofertę dostępną w magazynie z ceną
    for offer in offers:
        if offer.get("price") is not None and (
            parse_availability(str(offer.get("availability") or "")) == AvailabilityStatus.IN_STOCK
        ):
            return offer
    return offers[0]


def extract_json_ld(
    tree: HTMLParser,
    *,
    variant_label: str = "",
    default_currency: str | None = None,
) -> ExtractedOffer | None:
    """Ekstrahuje dane produktu z bloków <script type='application/ld+json'>."""
    scripts = tree.css('script[type="application/ld+json"]')
    candidates: list[dict[str, Any]] = []

    for script in scripts:
        raw_text = script.text(strip=True)
        if not raw_text:
            continue
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            continue
        candidates.extend(_collect_product_nodes(payload))

    if not candidates:
        return None

    # Jeśli podano wariant, preferujemy produkt/wariant pasujący do etykiety
    selected_product = candidates[0]
    if variant_label:
        needle = variant_label.strip().lower()
        for node in candidates:
            node_text = " ".join(
                str(node.get(k) or "") for k in ("name", "sku", "color", "size", "url")
            ).lower()
            if needle in node_text and node.get("offers"):
                selected_product = node
                break

    # Zbierz oferty ze wskazanego produktu (lub z pierwszego produktu z ofertami)
    raw_offers = _as_list(selected_product.get("offers"))
    if not raw_offers:
        for node in candidates:
            if node.get("offers"):
                selected_product = node
                raw_offers = _as_list(node.get("offers"))
                break

    flat_offers: list[dict[str, Any]] = []
    for item in raw_offers:
        if not isinstance(item, dict):
            continue
        if _has_type(item, {"aggregateoffer"}) and item.get("offers"):
            nested = [x for x in _as_list(item.get("offers")) if isinstance(x, dict)]
            flat_offers.extend(nested or [item])
        else:
            flat_offers.append(item)

    best_offer = _pick_best_offer(flat_offers, variant_label)
    if best_offer is None:
        return None

    current_price, regular_price, currency, availability = _extract_price_from_offer(
        best_offer, default_currency
    )
    if current_price is None:
        return None

    title = str(selected_product.get("name") or "").strip() or None
    image_url = _extract_image(selected_product)

    return ExtractedOffer(
        title=title,
        current_price=current_price,
        regular_price=regular_price,
        currency=currency,
        availability=availability,
        image_url=image_url,
        variant_label=variant_label or None,
        extraction_layer=ExtractionLayer.JSON_LD,
        confidence=0.95,
    )
