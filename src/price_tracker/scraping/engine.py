from __future__ import annotations

from price_tracker.core.enums import ErrorCode
from price_tracker.core.ssrf import extract_domain, normalize_url
from price_tracker.logging.setup import get_logger
from price_tracker.scraping.adapters.registry import AdapterRegistry, build_default_registry
from price_tracker.scraping.browser import BrowserFetcher
from price_tracker.scraping.http_client import SafeHttpClient
from price_tracker.scraping.models import FetchResult, ScrapeOutcome, StoreScrapeConfig
from price_tracker.scraping.parsers.price_parser import validate_price_sanity

logger = get_logger(__name__)

_HARD_BLOCK_ERRORS = frozenset(
    {
        ErrorCode.SSRF_BLOCKED,
        ErrorCode.BLOCKED_WAF,
        ErrorCode.BLOCKED_CAPTCHA,
        ErrorCode.AUTH_REQUIRED,
        ErrorCode.RATE_LIMITED,
    }
)


class ScrapingEngine:
    """Koordynuje pobieranie HTTP, fallback Playwright, adaptery i Sanity Guard."""

    def __init__(
        self,
        *,
        http_client: SafeHttpClient | None = None,
        browser_fetcher: BrowserFetcher | None = None,
        registry: AdapterRegistry | None = None,
        enable_browser_fallback: bool = True,
    ) -> None:
        self.http_client = http_client or SafeHttpClient()
        self.browser_fetcher = browser_fetcher
        self.registry = registry or build_default_registry()
        self._enable_browser_fallback = enable_browser_fallback

    async def scrape(self, config: StoreScrapeConfig) -> ScrapeOutcome:
        adapter = self.registry.resolve(config.domain, preferred_key=config.adapter_key)
        use_browser_directly = config.requires_js or adapter.prefer_js
        fetch_result: FetchResult | None = None

        # Krok 1: lekkie pobranie HTTP (chyba że sklep jawnie wymaga JS)
        if not use_browser_directly:
            fetch_result = await self.http_client.fetch(config.url)
            if fetch_result.error_code != ErrorCode.NONE:
                # Nie uruchamiamy Playwrighta w celu obchodzenia WAF / CAPTCHA / 429 / 404
                if fetch_result.error_code in _HARD_BLOCK_ERRORS or (
                    fetch_result.status_code is not None and 400 <= fetch_result.status_code < 500
                ):
                    return ScrapeOutcome(
                        ok=False,
                        fetch=fetch_result,
                        error_code=fetch_result.error_code,
                        error_detail=fetch_result.error_detail,
                        adapter_key=adapter.adapter_key,
                        diagnostics={
                            "stage": "http_fetch",
                            "status_code": fetch_result.status_code,
                        },
                    )
                return ScrapeOutcome(
                    ok=False,
                    fetch=fetch_result,
                    error_code=fetch_result.error_code,
                    error_detail=fetch_result.error_detail,
                    adapter_key=adapter.adapter_key,
                    diagnostics={"stage": "http_fetch"},
                )

            extracted = adapter.extract_from_html(fetch_result.html, config, from_browser=False)
            if extracted is not None and extracted.current_price is not None:
                return self._finalize_offer(extracted, fetch_result, config, adapter.adapter_key)

        # Krok 2: fallback na Playwright (renderowanie JS / wybór wariantu)
        if self._enable_browser_fallback and self.browser_fetcher is not None:
            wait_selector = (config.custom_selectors or {}).get("price")
            click_selector = (
                str(config.variant_selector["click_selector"])
                if config.variant_selector and "click_selector" in config.variant_selector
                else None
            )
            browser_fetch = await self.browser_fetcher.fetch(
                config.url,
                wait_selector=wait_selector,
                click_selector=click_selector,
            )
            fetch_result = browser_fetch
            if browser_fetch.error_code != ErrorCode.NONE:
                return ScrapeOutcome(
                    ok=False,
                    fetch=browser_fetch,
                    error_code=browser_fetch.error_code,
                    error_detail=browser_fetch.error_detail,
                    adapter_key=adapter.adapter_key,
                    diagnostics={"stage": "playwright_fetch"},
                )

            extracted = adapter.extract_from_html(browser_fetch.html, config, from_browser=True)
            if extracted is not None and extracted.current_price is not None:
                return self._finalize_offer(extracted, browser_fetch, config, adapter.adapter_key)

        return ScrapeOutcome(
            ok=False,
            fetch=fetch_result,
            error_code=ErrorCode.PARSE_PRICE_NOT_FOUND,
            error_detail=(
                "Pobrano stronę, ale żadna warstwa ekstrakcji nie rozpoznała poprawnej ceny. "
                "Sklep może wymagać wyboru wariantu lub zdefiniowania selektora CSS."
            ),
            adapter_key=adapter.adapter_key,
            diagnostics={
                "stage": "extraction",
                "used_browser": bool(fetch_result and fetch_result.used_browser),
            },
        )

    def _finalize_offer(
        self,
        extracted: object,
        fetch_result: FetchResult,
        config: StoreScrapeConfig,
        adapter_key: str,
    ) -> ScrapeOutcome:
        from price_tracker.scraping.models import ExtractedOffer

        assert isinstance(extracted, ExtractedOffer)
        assert extracted.current_price is not None

        sanity_code, sanity_msg = validate_price_sanity(
            extracted.current_price,
            extracted.currency,
            previous_price=config.previous_price,
            previous_currency=config.previous_currency,
        )
        if sanity_code != ErrorCode.NONE:
            return ScrapeOutcome(
                ok=False,
                offer=extracted,
                fetch=fetch_result,
                error_code=sanity_code,
                error_detail=sanity_msg,
                adapter_key=adapter_key,
                extraction_layer=extracted.extraction_layer,
                diagnostics={
                    "stage": "sanity_guard",
                    "candidate_price": str(extracted.current_price),
                    "candidate_currency": extracted.currency,
                },
            )

        return ScrapeOutcome(
            ok=True,
            offer=extracted,
            fetch=fetch_result,
            error_code=ErrorCode.NONE,
            adapter_key=adapter_key,
            extraction_layer=extracted.extraction_layer,
            diagnostics={"stage": "ok", "confidence": extracted.confidence},
        )

    async def preview_url(self, raw_url: str, *, variant_label: str = "") -> ScrapeOutcome:
        """Jednorazowe rozpoznanie danych produktu z URL (używane przy dodawaniu produktu)."""
        normalized = normalize_url(raw_url)
        domain = extract_domain(normalized)
        config = StoreScrapeConfig(
            url=normalized,
            domain=domain,
            variant_label=variant_label,
        )
        return await self.scrape(config)

    async def close(self) -> None:
        await self.http_client.close()
        if self.browser_fetcher is not None:
            await self.browser_fetcher.close()
