from __future__ import annotations

from selectolax.parser import HTMLParser

from price_tracker.core.enums import ExtractionLayer
from price_tracker.scraping.models import ExtractedOffer
from price_tracker.scraping.parsers.price_parser import (
    normalize_currency,
    parse_availability,
    parse_price,
)


def _meta(tree: HTMLParser, *names: str) -> str | None:
    for name in names:
        for attr in ("property", "name"):
            node = tree.css_first(f'meta[{attr}="{name}"]')
            if node is not None:
                content = node.attributes.get("content")
                if content and content.strip():
                    return content.strip()
    return None


def extract_opengraph(
    tree: HTMLParser,
    *,
    default_currency: str | None = None,
) -> ExtractedOffer | None:
    """Ekstrahuje dane produktu z metadanych Open Graph / Twitter / Product Meta."""
    title = _meta(tree, "og:title", "twitter:title")
    image_url = _meta(tree, "og:image", "twitter:image")
    store_name = _meta(tree, "og:site_name")

    raw_currency = _meta(
        tree,
        "product:price:currency",
        "og:price:currency",
        "product:sale_price:currency",
    )
    currency_hint = normalize_currency(raw_currency) or default_currency

    raw_sale = _meta(tree, "product:sale_price:amount")
    raw_price = _meta(tree, "product:price:amount", "og:price:amount")
    raw_orig = _meta(tree, "product:original_price:amount")

    primary_raw = raw_sale or raw_price
    if not primary_raw:
        return None

    parsed = parse_price(primary_raw, default_currency=currency_hint)
    if parsed is None:
        return None

    current_price, currency = parsed
    regular_price = None

    regular_candidate = raw_orig or (raw_price if raw_sale and raw_price != raw_sale else None)
    if regular_candidate:
        parsed_reg = parse_price(regular_candidate, default_currency=currency)
        if parsed_reg and parsed_reg[0] > current_price:
            regular_price = parsed_reg[0]

    availability = parse_availability(
        _meta(tree, "product:availability", "og:availability")
    )

    valid_image = (
        image_url if image_url and image_url.startswith(("http://", "https://")) else None
    )

    return ExtractedOffer(
        title=title,
        current_price=current_price,
        regular_price=regular_price,
        currency=currency,
        availability=availability,
        image_url=valid_image,
        store_name=store_name,
        extraction_layer=ExtractionLayer.OPEN_GRAPH,
        confidence=0.80,
    )
