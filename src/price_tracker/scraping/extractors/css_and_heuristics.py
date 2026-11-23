from __future__ import annotations

import re

from selectolax.parser import HTMLParser, Node

from price_tracker.core.enums import ExtractionLayer
from price_tracker.scraping.models import ExtractedOffer
from price_tracker.scraping.parsers.price_parser import (
    normalize_currency,
    parse_availability,
    parse_price,
)

_EXCLUDED_CONTEXT_RE = re.compile(
    r"(?i)(rat[ay]|miesięcz|miesiecz|installment|/mies|dostaw|wysyłk|shipping|"
    r"za\s*szt|/\s*100\s*ml|/\s*kg|related|similar|recommend|cross[-_]?sell|upsell|"
    r"ostatnio\s*ogląd|podobne\s*produkty|inne\s*produkty)"
)

_HEURISTIC_PRICE_SELECTORS = (
    "[data-price]",
    "[data-product-price]",
    '[data-testid*="price"]',
    '[class*="product-price"]',
    '[class*="current-price"]',
    '[class*="sale-price"]',
    '[class*="price-current"]',
    '[id*="product-price"]',
)


def _first_text(tree: HTMLParser, selector: str | None) -> str | None:
    if not selector:
        return None
    node = tree.css_first(selector)
    if node is None:
        return None
    for attr in ("content", "data-price", "value"):
        val = node.attributes.get(attr)
        if val and val.strip():
            return val.strip()
    text = node.text(strip=True)
    return text or None


def extract_with_css_selectors(
    tree: HTMLParser,
    selectors: dict[str, str],
    *,
    default_currency: str | None = None,
    layer: ExtractionLayer = ExtractionLayer.CSS_SELECTOR,
    confidence: float = 0.90,
) -> ExtractedOffer | None:
    """Ekstrahuje dane produktu przy pomocy zdefiniowanych selektorów CSS."""
    raw_price = _first_text(tree, selectors.get("price"))
    if not raw_price:
        return None

    currency_hint = (
        normalize_currency(_first_text(tree, selectors.get("currency"))) or default_currency
    )
    parsed = parse_price(raw_price, default_currency=currency_hint)
    if parsed is None:
        return None

    current_price, currency = parsed
    regular_price = None
    raw_reg = _first_text(tree, selectors.get("regular_price"))
    if raw_reg:
        parsed_reg = parse_price(raw_reg, default_currency=currency)
        if parsed_reg and parsed_reg[0] > current_price:
            regular_price = parsed_reg[0]

    title = _first_text(tree, selectors.get("title"))
    availability = parse_availability(_first_text(tree, selectors.get("availability")))

    image_url: str | None = None
    img_sel = selectors.get("image")
    if img_sel:
        img_node = tree.css_first(img_sel)
        if img_node is not None:
            candidate = img_node.attributes.get("src") or img_node.attributes.get("content")
            if candidate and candidate.startswith(("http://", "https://")):
                image_url = candidate

    return ExtractedOffer(
        title=title,
        current_price=current_price,
        regular_price=regular_price,
        currency=currency,
        availability=availability,
        image_url=image_url,
        extraction_layer=layer,
        confidence=confidence,
    )


def _is_excluded_node(node: Node) -> bool:
    """Odrzuca węzły z kontekstem rat, kosztów dostawy i sekcji podobnych produktów."""
    text = node.text(strip=True)
    if _EXCLUDED_CONTEXT_RE.search(text):
        return True

    current: Node | None = node
    for _ in range(5):
        if current is None:
            break
        attrs_blob = " ".join(
            str(current.attributes.get(k) or "")
            for k in ("class", "id", "data-testid", "aria-label")
        )
        if _EXCLUDED_CONTEXT_RE.search(attrs_blob):
            return True
        current = current.parent
    return False


def extract_heuristic(
    tree: HTMLParser,
    *,
    default_currency: str | None = None,
) -> ExtractedOffer | None:
    """Heurystyczny fallback HTML dla sklepów bez JSON-LD/Microdata/OpenGraph."""
    h1_node = tree.css_first("h1")
    title = h1_node.text(strip=True) if h1_node is not None else None

    for selector in _HEURISTIC_PRICE_SELECTORS:
        for node in tree.css(selector):
            if _is_excluded_node(node):
                continue

            raw = (
                node.attributes.get("data-price")
                or node.attributes.get("data-product-price")
                or node.text(strip=True)
            )
            if not raw or len(raw) > 64:
                continue

            parsed = parse_price(raw, default_currency=default_currency)
            if parsed is None:
                continue

            current_price, currency = parsed
            if currency is None:
                # W heurystyce wymagamy rozpoznanej waluty (lub wskazówki domenowej),
                # aby uniknąć mylenia losowych liczb (SKU, waga, rozmiar) z ceną.
                continue

            return ExtractedOffer(
                title=title,
                current_price=current_price,
                currency=currency,
                extraction_layer=ExtractionLayer.HEURISTIC,
                confidence=0.60,
            )

    return None
