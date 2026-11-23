from __future__ import annotations

from price_tracker.core.enums import ErrorCode
from price_tracker.scraping.diagnostics import classify_http_response, is_error_retryable


def test_detects_cloudflare_waf() -> None:
    verdict = classify_http_response(
        403,
        {"server": "cloudflare", "cf-mitigated": "challenge"},
        "<html><title>Just a moment...</title></html>",
    )
    assert verdict is not None
    assert verdict.error_code == ErrorCode.BLOCKED_WAF
    assert verdict.retryable is False


def test_detects_captcha() -> None:
    verdict = classify_http_response(
        403,
        {"content-type": "text/html"},
        '<html><body><div class="cf-turnstile">Verify you are human</div></body></html>',
    )
    assert verdict is not None
    assert verdict.error_code == ErrorCode.BLOCKED_CAPTCHA
    assert verdict.retryable is False


def test_detects_rate_limit_429_as_retryable() -> None:
    verdict = classify_http_response(429, {"retry-after": "30"}, "Too Many Requests")
    assert verdict is not None
    assert verdict.error_code == ErrorCode.RATE_LIMITED
    assert verdict.retryable is True
    assert is_error_retryable(verdict.error_code, 429) is True


def test_detects_404_as_non_retryable() -> None:
    verdict = classify_http_response(404, {}, "Not Found")
    assert verdict is not None
    assert verdict.error_code == ErrorCode.HTTP_ERROR
    assert verdict.retryable is False
    assert is_error_retryable(verdict.error_code, 404) is False
