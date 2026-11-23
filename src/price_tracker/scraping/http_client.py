from __future__ import annotations

import time
from urllib.parse import urljoin

import httpx

from price_tracker.core.enums import ErrorCode
from price_tracker.core.exceptions import ValidationError
from price_tracker.core.ssrf import HostResolver, SSRFBlockedError, validate_url_ssrf
from price_tracker.scraping.diagnostics import classify_http_response
from price_tracker.scraping.models import FetchResult

DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
}

MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MB


class SafeHttpClient:
    """Asynchroniczny klient HTTP z walidacją SSRF na każdym przekierowaniu i limitami zasobów."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        max_redirects: int = MAX_REDIRECTS,
        resolver: HostResolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._max_redirects = max_redirects
        self._resolver = resolver
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
            headers=DEFAULT_HEADERS,
            follow_redirects=False,
            http2=False,
            transport=transport,
        )

    async def fetch(
        self,
        url: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> FetchResult:
        started = time.perf_counter()
        current_url = url
        redirects = 0

        try:
            while True:
                validated_url = await validate_url_ssrf(current_url, resolver=self._resolver)
                response = await self._client.get(validated_url, headers=extra_headers)

                if response.is_redirect and "location" in response.headers:
                    redirects += 1
                    if redirects > self._max_redirects:
                        return FetchResult(
                            url=url,
                            final_url=validated_url,
                            status_code=response.status_code,
                            duration_ms=self._elapsed_ms(started),
                            error_code=ErrorCode.HTTP_ERROR,
                            error_detail=(
                                f"Przekroczono limit przekierowań ({self._max_redirects})."
                            ),
                        )
                    current_url = urljoin(validated_url, response.headers["location"])
                    continue

                raw_bytes = response.content[:MAX_RESPONSE_BYTES]
                encoding = response.encoding or "utf-8"
                html = raw_bytes.decode(encoding, errors="replace")
                headers_dict = dict(response.headers)
                duration_ms = self._elapsed_ms(started)

                verdict = classify_http_response(response.status_code, headers_dict, html)
                if verdict is not None:
                    return FetchResult(
                        url=url,
                        final_url=validated_url,
                        status_code=response.status_code,
                        headers=headers_dict,
                        html=html,
                        duration_ms=duration_ms,
                        error_code=verdict.error_code,
                        error_detail=verdict.detail,
                    )

                return FetchResult(
                    url=url,
                    final_url=validated_url,
                    status_code=response.status_code,
                    headers=headers_dict,
                    html=html,
                    duration_ms=duration_ms,
                )

        except SSRFBlockedError as exc:
            return FetchResult(
                url=url,
                final_url=current_url,
                status_code=None,
                duration_ms=self._elapsed_ms(started),
                error_code=ErrorCode.SSRF_BLOCKED,
                error_detail=str(exc),
            )
        except ValidationError as exc:
            return FetchResult(
                url=url,
                final_url=current_url,
                status_code=None,
                duration_ms=self._elapsed_ms(started),
                error_code=ErrorCode.NETWORK_ERROR,
                error_detail=str(exc),
            )
        except httpx.TimeoutException as exc:
            return FetchResult(
                url=url,
                final_url=current_url,
                status_code=None,
                duration_ms=self._elapsed_ms(started),
                error_code=ErrorCode.TIMEOUT,
                error_detail=f"Przekroczono czas oczekiwania na odpowiedź sklepu: {exc}",
            )
        except httpx.HTTPError as exc:
            return FetchResult(
                url=url,
                final_url=current_url,
                status_code=None,
                duration_ms=self._elapsed_ms(started),
                error_code=ErrorCode.NETWORK_ERROR,
                error_detail=f"Błąd sieci podczas pobierania strony: {exc}",
            )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)

    async def close(self) -> None:
        await self._client.aclose()
