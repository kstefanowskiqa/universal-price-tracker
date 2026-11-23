from __future__ import annotations

import pytest

from price_tracker.core.ssrf import (
    SSRFBlockedError,
    extract_domain,
    is_ip_public,
    normalize_url,
    validate_url_ssrf,
)


async def _fake_public_resolver(host: str, port: int) -> list[str]:
    return ["93.184.216.34"]


async def _fake_private_resolver(host: str, port: int) -> list[str]:
    return ["10.0.0.12"]


async def _fake_mixed_resolver(host: str, port: int) -> list[str]:
    return ["93.184.216.34", "169.254.169.254"]


def test_normalize_url_and_extract_domain() -> None:
    url = normalize_url("HTTPS://WWW.X-KOM.PL:443/p/12345.html#sekcja")
    assert url == "https://www.x-kom.pl/p/12345.html"
    assert extract_domain(url) == "x-kom.pl"


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.10",
        "169.254.169.254",
        "::1",
        "::ffff:127.0.0.1",
        "fc00::1",
        "0.0.0.0",
    ],
)
def test_private_ips_rejected(ip: str) -> None:
    assert is_ip_public(ip) is False


def test_public_ip_allowed() -> None:
    assert is_ip_public("93.184.216.34") is True


async def test_validate_url_ssrf_allows_public_domain() -> None:
    result = await validate_url_ssrf(
        "https://www.x-kom.pl/p/123-produkt.html",
        resolver=_fake_public_resolver,
    )
    assert result == "https://www.x-kom.pl/p/123-produkt.html"


@pytest.mark.parametrize(
    "bad_url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "http://localhost:8000/admin",
        "http://127.0.0.1/secret",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/test",
        "https://user:pass@example.com/product",
        "https://internal.service.local/item",
    ],
)
async def test_validate_url_ssrf_blocks_dangerous_urls(bad_url: str) -> None:
    with pytest.raises(SSRFBlockedError):
        await validate_url_ssrf(bad_url, resolver=_fake_public_resolver)


async def test_validate_url_ssrf_blocks_dns_rebinding_to_private_ip() -> None:
    with pytest.raises(SSRFBlockedError):
        await validate_url_ssrf("https://evil.example.com/p/1", resolver=_fake_private_resolver)

    with pytest.raises(SSRFBlockedError):
        await validate_url_ssrf("https://dual.example.com/p/1", resolver=_fake_mixed_resolver)
