from __future__ import annotations

from selectolax.parser import HTMLParser

from price_tracker.core.enums import ExtractionLayer
from price_tracker.scraping.models import ExtractedOffer
from price_tracker.scraping.parsers.price_parser import (
    normalize_currency,
    parse_availability,
    parse_price,
)


def _node_value(tree: HTMLParser, selector: str) -> str | None:
    node = tree.css_first(selector)
    if node is None:
        return None
    for attr in ("content", "href", "src", "value"):
        val = node.attributes.get(attr)
        if val and val.strip():
            return val.strip()
    text = node.text(strip=True)
    return text or None


def extract_microdata(
    tree: HTMLParser,
    *,
    default_currency: str | None = None,
) -> ExtractedOffer | None:
    """Ekstrahuje dane produktu z atrybutów HTML Microdata (itemprop='price' itp.)."""
    raw_price = _node_value(tree, '[itemprop="price"]')
    if not raw_price:
        return None

    raw_currency = _node_value(tree, '[itemprop="priceCurrency"]')
    currency_hint = normalize_currency(raw_currency) or default_currency
    parsed = parse_price(raw_price, default_currency=currency_hint)
    if parsed is None:
        return None

    current_price, currency = parsed
    title = _node_value(tree, '[itemprop="name"]')
    availability = parse_availability(_node_value(tree, '[itemprop="availability"]'))
    image_url = _node_value(tree, '[itemprop="image"]')

    valid_image = (
        image_url if image_url and image_url.startswith(("http://", "https://")) else None
    )

    return ExtractedOffer(
        title=title,
        current_price=current_price,
        currency=currency,
        availability=availability,
        image_url=valid_image,
        extraction_layer=ExtractionLayer.MICRODATA,
        confidence=0.85,
    )
