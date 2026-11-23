from __future__ import annotations

from dataclasses import dataclass

from price_tracker.core.enums import ErrorCode

_CLOUDFLARE_MARKERS = (
    "cf-browser-verification",
    "cf_chl_opt",
    "__cf_chl_tk",
    "cdn-cgi/challenge-platform",
    "just a moment...",
    "attention required! | cloudflare",
    "checking your browser before accessing",
)

_WAF_MARKERS = (
    "access denied",
    "reference #18.",  # Akamai EdgeSuite
    "akamai ghost",
    "datadome",
    "px-captcha",
    "perimeterx",
    "incapsula incident id",
    "request unsuccessful. incapsula",
    "bot detection",
)

_CAPTCHA_MARKERS = (
    "g-recaptcha",
    "h-captcha",
    "cf-turnstile",
    "verify you are human",
    "potwierdź, że jesteś człowiekiem",
    "are you a robot",
    "captcha-delivery.com",
)

_AUTH_MARKERS = (
    "sign in to continue",
    "zaloguj się, aby kontynuować",
    "login required",
)

TRANSIENT_ERROR_CODES = frozenset(
    {
        ErrorCode.TIMEOUT,
        ErrorCode.NETWORK_ERROR,
        ErrorCode.RATE_LIMITED,
    }
)


@dataclass(frozen=True, slots=True)
class DiagnosticVerdict:
    error_code: ErrorCode
    detail: str
    retryable: bool


def classify_http_response(
    status_code: int,
    headers: dict[str, str],
    body: str,
) -> DiagnosticVerdict | None:
    """Klasyfikuje odpowiedzi błędne, WAF, CAPTCHA i rate-limiting bez prób ich omijania."""
    lower_headers = {k.lower(): v.lower() for k, v in headers.items()}
    sample = body[:25_000].lower()

    if status_code == 429:
        retry_after = lower_headers.get("retry-after")
        suffix = f" (Retry-After: {retry_after}s)" if retry_after else ""
        return DiagnosticVerdict(
            error_code=ErrorCode.RATE_LIMITED,
            detail=(
                f"Sklep ograniczył częstotliwość zapytań (HTTP 429){suffix}. "
                "Zwiększ interwał sprawdzania dla tej domeny."
            ),
            retryable=True,
        )

    if status_code == 401:
        return DiagnosticVerdict(
            error_code=ErrorCode.AUTH_REQUIRED,
            detail="Strona wymaga zalogowania (HTTP 401). Aplikacja nie obchodzi logowania.",
            retryable=False,
        )

    # CAPTCHA w treści strony (także przy 403/503/200)
    if any(marker in sample for marker in _CAPTCHA_MARKERS) and (
        status_code in {403, 429, 503} or len(body) < 120_000
    ):
        return DiagnosticVerdict(
            error_code=ErrorCode.BLOCKED_CAPTCHA,
            detail=(
                f"Wykryto wyzwanie CAPTCHA (HTTP {status_code}). "
                "Aplikacja nie omija CAPTCHA – użyj oficjalnego API sklepu lub innego sprzedawcy."
            ),
            retryable=False,
        )

    # Cloudflare / Akamai / DataDome / PerimeterX
    is_cf_mitigated = lower_headers.get("cf-mitigated") == "challenge"
    server_hdr = lower_headers.get("server", "")
    has_cf_marker = any(marker in sample for marker in _CLOUDFLARE_MARKERS)
    has_waf_marker = any(marker in sample for marker in _WAF_MARKERS)

    if is_cf_mitigated or (status_code in {403, 503} and (has_cf_marker or has_waf_marker)):
        is_cf = is_cf_mitigated or "cloudflare" in server_hdr or has_cf_marker
        vendor = "Cloudflare" if is_cf else "WAF"
        return DiagnosticVerdict(
            error_code=ErrorCode.BLOCKED_WAF,
            detail=(
                f"Żądanie zablokowane przez ochronę {vendor} (HTTP {status_code}). "
                "Aplikacja nie obchodzi zabezpieczeń antybotowych – rozważ dłuższy interwał "
                "lub oficjalne API sklepu."
            ),
            retryable=False,
        )

    if status_code == 403:
        return DiagnosticVerdict(
            error_code=ErrorCode.BLOCKED_WAF,
            detail=(
                "Odmowa dostępu (HTTP 403) – prawdopodobna blokada antybotowa lub regionalna "
                "po stronie sklepu."
            ),
            retryable=False,
        )

    if status_code >= 500:
        return DiagnosticVerdict(
            error_code=ErrorCode.HTTP_ERROR,
            detail=f"Tymczasowy błąd serwera sklepu (HTTP {status_code}).",
            retryable=True,
        )

    if status_code >= 400:
        return DiagnosticVerdict(
            error_code=ErrorCode.HTTP_ERROR,
            detail=f"Błąd HTTP {status_code} (strona produktu może już nie istnieć).",
            retryable=False,
        )

    # Strona 200 OK, ale faktycznie brama logowania zamiast karty produktu
    if len(body) < 40_000 and any(marker in sample for marker in _AUTH_MARKERS):
        return DiagnosticVerdict(
            error_code=ErrorCode.AUTH_REQUIRED,
            detail="Strona wymaga zalogowania do podglądu oferty.",
            retryable=False,
        )

    return None


def is_error_retryable(error_code: ErrorCode, http_status: int | None = None) -> bool:
    if error_code in TRANSIENT_ERROR_CODES:
        return True
    return error_code == ErrorCode.HTTP_ERROR and http_status is not None and http_status >= 500
