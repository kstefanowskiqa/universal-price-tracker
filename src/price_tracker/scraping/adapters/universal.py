from __future__ import annotations

from selectolax.parser import HTMLParser

from price_tracker.core.enums import AvailabilityStatus, ExtractionLayer
from price_tracker.scraping.adapters.base import BaseStoreAdapter
from price_tracker.scraping.extractors import (
    extract_heuristic,
    extract_json_ld,
    extract_microdata,
    extract_opengraph,
    extract_with_css_selectors,
)
from price_tracker.scraping.models import ExtractedOffer, StoreScrapeConfig


def enrich_metadata(primary: ExtractedOffer, tree: HTMLParser) -> ExtractedOffer:
    """Uzupełnia brakujące pola (tytuł, zdjęcie, nazwa sklepu) z OpenGraph / <title>."""
    if not primary.title or not primary.image_url or not primary.store_name:
        og_title = tree.css_first('meta[property="og:title"]')
        og_img = tree.css_first('meta[property="og:image"]')
        og_site = tree.css_first('meta[property="og:site_name"]')

        if not primary.title and og_title is not None:
            primary.title = (og_title.attributes.get("content") or "").strip() or None
        if not primary.title:
            h1 = tree.css_first("h1")
            if h1 is not None:
                primary.title = h1.text(strip=True) or None
        if not primary.title:
            title_tag = tree.css_first("title")
            if title_tag is not None:
                primary.title = title_tag.text(strip=True) or None

        if not primary.image_url and og_img is not None:
            cand = (og_img.attributes.get("content") or "").strip()
            if cand.startswith(("http://", "https://")):
                primary.image_url = cand

        if not primary.store_name and og_site is not None:
            primary.store_name = (og_site.attributes.get("content") or "").strip() or None

    return primary


class UniversalAdapter(BaseStoreAdapter):
    """Adapter wielowarstwowy: CSS -> JSON-LD -> Microdata -> Open Graph -> Heurystyki."""

    adapter_key = "universal"
    display_name = "Universal Multi-Layer Adapter"

    def extract_from_html(
        self,
        html: str,
        config: StoreScrapeConfig,
        *,
        from_browser: bool = False,
    ) -> ExtractedOffer | None:
        tree = self.parse_tree(html)
        default_currency = self.infer_default_currency(config.url) or config.previous_currency

        result: ExtractedOffer | None = None

        # Warstwa 0: jawne selektory CSS skonfigurowane dla sklepu przez użytkownika
        if config.custom_selectors:
            result = extract_with_css_selectors(
                tree,
                config.custom_selectors,
                default_currency=default_currency,
            )

        # Warstwa 1: JSON-LD (Schema.org)
        if result is None:
            result = extract_json_ld(
                tree,
                variant_label=config.variant_label,
                default_currency=default_currency,
            )

        # Warstwa 2: Microdata
        if result is None:
            result = extract_microdata(tree, default_currency=default_currency)

        # Warstwa 3: Open Graph
        if result is None:
            result = extract_opengraph(tree, default_currency=default_currency)

        # Warstwa 4: Heurystyka semantyczna HTML
        if result is None:
            result = extract_heuristic(tree, default_currency=default_currency)

        if result is None:
            return None

        result = enrich_metadata(result, tree)
        result.adapter_key = self.adapter_key
        if from_browser:
            result.extraction_layer = ExtractionLayer.PLAYWRIGHT
        if not result.store_name:
            result.store_name = config.domain
        if result.availability == AvailabilityStatus.UNKNOWN:
            # Dodatkowe sprawdzenie meta og:availability jeśli główna warstwa go nie podała
            og_offer = extract_opengraph(tree, default_currency=default_currency)
            if og_offer and og_offer.availability != AvailabilityStatus.UNKNOWN:
                result.availability = og_offer.availability
        return result
