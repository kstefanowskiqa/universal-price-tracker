from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from typing import Any

from price_tracker.core.enums import ErrorCode
from price_tracker.core.ssrf import HostResolver, SSRFBlockedError, validate_url_ssrf
from price_tracker.logging.setup import get_logger
from price_tracker.scraping.diagnostics import classify_http_response
from price_tracker.scraping.models import FetchResult

logger = get_logger(__name__)

_BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})


class BrowserFetcher:
    """Leniwy manager Playwright (Chromium Headless) używany wyłącznie jako fallback dla SPA/JS."""

    def __init__(
        self,
        *,
        max_concurrent: int = 2,
        timeout_ms: int = 25_000,
        resolver: HostResolver | None = None,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._timeout_ms = timeout_ms
        self._resolver = resolver
        self._lock = asyncio.Lock()
        self._playwright: Any = None
        self._browser: Any = None

    async def _ensure_browser(self) -> Any:
        async with self._lock:
            if self._browser is not None:
                return self._browser
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise RuntimeError(
                    "Playwright nie jest zainstalowany. Uruchom: pip install -e '.[scraping]'"
                ) from exc

            self._playwright = await async_playwright().start()
            launch_args = ["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"]
            try:
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=launch_args,
                )
            except Exception:
                if sys.platform != "win32":
                    raise
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    channel="msedge",
                    args=launch_args,
                )
            logger.info("playwright_browser_started")
            return self._browser

    async def fetch(
        self,
        url: str,
        *,
        wait_selector: str | None = None,
        click_selector: str | None = None,
    ) -> FetchResult:
        started = time.perf_counter()
        try:
            validated_url = await validate_url_ssrf(url, resolver=self._resolver)
        except SSRFBlockedError as exc:
            return FetchResult(
                url=url,
                final_url=url,
                status_code=None,
                used_browser=True,
                duration_ms=int((time.perf_counter() - started) * 1000),
                error_code=ErrorCode.SSRF_BLOCKED,
                error_detail=str(exc),
            )
        except Exception as exc:
            return FetchResult(
                url=url,
                final_url=url,
                status_code=None,
                used_browser=True,
                duration_ms=int((time.perf_counter() - started) * 1000),
                error_code=ErrorCode.NETWORK_ERROR,
                error_detail=str(exc),
            )

        async with self._semaphore:
            context: Any = None
            try:
                browser = await self._ensure_browser()
                context = await browser.new_context(
                    locale="pl-PL",
                    viewport={"width": 1366, "height": 900},
                )
                page = await context.new_page()

                async def _route_guard(route: Any) -> None:
                    req = route.request
                    if req.resource_type in _BLOCKED_RESOURCE_TYPES:
                        await route.abort()
                        return
                    try:
                        await validate_url_ssrf(req.url, resolver=self._resolver)
                    except Exception:
                        await route.abort()
                        return
                    await route.continue_()

                await page.route("**/*", _route_guard)

                response = await page.goto(
                    validated_url,
                    wait_until="domcontentloaded",
                    timeout=self._timeout_ms,
                )

                if click_selector:
                    locator = page.locator(click_selector).first
                    await locator.click(timeout=5_000)

                if wait_selector:
                    await page.wait_for_selector(wait_selector, timeout=8_000)
                else:
                    with contextlib.suppress(Exception):
                        await page.wait_for_load_state("networkidle", timeout=6_000)

                html = await page.content()
                final_url = str(page.url)
                status_code = response.status if response is not None else 200
                headers = dict(response.headers) if response is not None else {}
                duration_ms = int((time.perf_counter() - started) * 1000)

                verdict = classify_http_response(status_code, headers, html)
                if verdict is not None:
                    return FetchResult(
                        url=url,
                        final_url=final_url,
                        status_code=status_code,
                        headers=headers,
                        html=html,
                        duration_ms=duration_ms,
                        used_browser=True,
                        error_code=verdict.error_code,
                        error_detail=verdict.detail,
                    )

                return FetchResult(
                    url=url,
                    final_url=final_url,
                    status_code=status_code,
                    headers=headers,
                    html=html,
                    duration_ms=duration_ms,
                    used_browser=True,
                )
            except TimeoutError as exc:
                return FetchResult(
                    url=url,
                    final_url=validated_url,
                    status_code=None,
                    used_browser=True,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    error_code=ErrorCode.TIMEOUT,
                    error_detail=f"Timeout Playwright podczas renderowania strony: {exc}",
                )
            except Exception as exc:
                return FetchResult(
                    url=url,
                    final_url=validated_url,
                    status_code=None,
                    used_browser=True,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    error_code=ErrorCode.NETWORK_ERROR,
                    error_detail=f"Błąd przeglądarki Playwright: {exc}",
                )
            finally:
                if context is not None:
                    await context.close()

    async def close(self) -> None:
        async with self._lock:
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None
            logger.info("playwright_browser_stopped")
