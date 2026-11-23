from __future__ import annotations

from abc import ABC, abstractmethod

from selectolax.parser import HTMLParser

from price_tracker.scraping.models import ExtractedOffer, StoreScrapeConfig


class BaseStoreAdapter(ABC):
    """Bazowy kontrakt dla adapterów sklepów internetowych."""

    adapter_key: str = "base"
    display_name: str = "Generic Store"
    supported_domains: tuple[str, ...] = ()
    default_currency: str | None = None
    prefer_js: bool = False

    def matches_domain(self, domain: str) -> bool:
        normalized = domain.lower().strip()
        return any(
            normalized == supported or normalized.endswith(f".{supported}")
            for supported in self.supported_domains
        )

    def infer_default_currency(self, url: str) -> str | None:
        return self.default_currency

    @abstractmethod
    def extract_from_html(
        self,
        html: str,
        config: StoreScrapeConfig,
        *,
        from_browser: bool = False,
    ) -> ExtractedOffer | None:
        """Analizuje dokument HTML i zwraca ExtractedOffer lub None, jeśli ceny nie znaleziono."""

    @staticmethod
    def parse_tree(html: str) -> HTMLParser:
        return HTMLParser(html)
