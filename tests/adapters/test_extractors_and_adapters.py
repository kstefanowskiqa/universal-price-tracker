from __future__ import annotations

from decimal import Decimal

import httpx

from price_tracker.core.enums import AvailabilityStatus, ErrorCode, ExtractionLayer
from price_tracker.scraping.adapters.registry import build_default_registry
from price_tracker.scraping.engine import ScrapingEngine
from price_tracker.scraping.http_client import SafeHttpClient
from price_tracker.scraping.models import StoreScrapeConfig

XKOM_HTML = """
<!doctype html>
<html>
<head>
  <meta property="og:image" content="https://cdn.x-kom.pl/gpu.jpg" />
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "Product",
    "name": "ASUS GeForce RTX 4070 SUPER 12GB",
    "offers": {
      "@type": "Offer",
      "price": "2699.00",
      "priceCurrency": "PLN",
      "availability": "https://schema.org/InStock"
    }
  }
  </script>
</head>
<body>
  <h1 data-name="productTitle">ASUS GeForce RTX 4070 SUPER 12GB</h1>
  <div data-name="productOldPrice">2 999,00 zł</div>
  <div data-name="productPrice">2 699,00 zł</div>
</body>
</html>
"""

HM_HTML = """
<!doctype html>
<html>
<head>
  <meta property="og:image" content="https://image.hm.com/coat.jpg" />
  <script id="__NEXT_DATA__" type="application/json">
  {
    "props": {
      "pageProps": {
        "product": {
          "name": "Płaszcz z domieszką wełny",
          "redPrice": {"value": "279.99"},
          "whitePrice": {"value": "399.99"},
          "inStock": true
        }
      }
    }
  }
  </script>
</head>
<body><h1>Płaszcz z domieszką wełny</h1></body>
</html>
"""

VS_HTML = """
<!doctype html>
<html>
<head>
  <meta property="og:title" content="Bombshell Eau de Parfum 100ml" />
  <meta property="product:sale_price:amount" content="249.00" />
  <meta property="product:price:amount" content="349.00" />
  <meta property="product:price:currency" content="PLN" />
  <meta property="product:availability" content="out of stock" />
</head>
<body><h1>Bombshell Eau de Parfum 100ml</h1></body>
</html>
"""

HEURISTIC_WITH_INSTALLMENT_HTML = """
<!doctype html>
<html>
<body>
  <h1>Ekspres do kawy Pro</h1>
  <div class="installment-price">Rata 0% 99,99 zł miesięcznie</div>
  <section class="related-products">
    <span class="product-price">49,00 zł</span>
  </section>
  <main>
    <span class="current-price">1 899,00 zł</span>
  </main>
</body>
</html>
"""


async def _public_dns(_host: str, _port: int) -> list[str]:
    return ["93.184.216.34"]


def test_xkom_adapter_extracts_promo_and_regular_price() -> None:
    registry = build_default_registry()
    adapter = registry.resolve("x-kom.pl")
    assert adapter.adapter_key == "xkom"

    cfg = StoreScrapeConfig(url="https://www.x-kom.pl/p/12345.html", domain="x-kom.pl")
    offer = adapter.extract_from_html(XKOM_HTML, cfg)
    assert offer is not None
    assert offer.title == "ASUS GeForce RTX 4070 SUPER 12GB"
    assert offer.current_price == Decimal("2699.00")
    assert offer.regular_price == Decimal("2999.00")
    assert offer.currency == "PLN"
    assert offer.availability == AvailabilityStatus.IN_STOCK
    assert offer.store_name == "x-kom"


def test_hm_adapter_extracts_next_data_and_locale_currency() -> None:
    registry = build_default_registry()
    adapter = registry.resolve("www2.hm.com")
    assert adapter.adapter_key == "hm"

    cfg = StoreScrapeConfig(
        url="https://www2.hm.com/pl_pl/productpage.123456.html",
        domain="www2.hm.com",
    )
    offer = adapter.extract_from_html(HM_HTML, cfg)
    assert offer is not None
    assert offer.current_price == Decimal("279.99")
    assert offer.regular_price == Decimal("399.99")
    assert offer.currency == "PLN"
    assert offer.availability == AvailabilityStatus.IN_STOCK


def test_victorias_secret_adapter_extracts_og_and_availability() -> None:
    registry = build_default_registry()
    adapter = registry.resolve("pl.victoriassecret.com")
    assert adapter.adapter_key == "victorias_secret"

    cfg = StoreScrapeConfig(
        url="https://pl.victoriassecret.com/pl/p/bombshell-100ml",
        domain="pl.victoriassecret.com",
    )
    offer = adapter.extract_from_html(VS_HTML, cfg)
    assert offer is not None
    assert offer.current_price == Decimal("249.00")
    assert offer.regular_price == Decimal("349.00")
    assert offer.currency == "PLN"
    assert offer.availability == AvailabilityStatus.OUT_OF_STOCK


def test_universal_heuristic_ignores_installments_and_related_products() -> None:
    registry = build_default_registry()
    adapter = registry.resolve("unknown-shop.pl")
    assert adapter.adapter_key == "universal"

    cfg = StoreScrapeConfig(url="https://unknown-shop.pl/ekspres", domain="unknown-shop.pl")
    offer = adapter.extract_from_html(HEURISTIC_WITH_INSTALLMENT_HTML, cfg)
    assert offer is not None
    assert offer.extraction_layer == ExtractionLayer.HEURISTIC
    assert offer.current_price == Decimal("1899.00")
    assert offer.currency == "PLN"


async def test_http_client_blocks_redirect_to_private_ip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "shop.example.com":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1:8765/health"})
        return httpx.Response(200, text="should never happen")

    client = SafeHttpClient(
        resolver=_public_dns,
        transport=httpx.MockTransport(handler),
    )
    try:
        res = await client.fetch("https://shop.example.com/product/1")
        assert res.error_code == ErrorCode.SSRF_BLOCKED
    finally:
        await client.close()


async def test_scraping_engine_handles_waf_without_corrupting_result() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"server": "cloudflare", "cf-mitigated": "challenge"},
            text="<html><title>Just a moment...</title></html>",
        )

    client = SafeHttpClient(resolver=_public_dns, transport=httpx.MockTransport(handler))
    engine = ScrapingEngine(http_client=client, enable_browser_fallback=False)
    try:
        outcome = await engine.preview_url("https://www.x-kom.pl/p/999.html")
        assert outcome.ok is False
        assert outcome.error_code == ErrorCode.BLOCKED_WAF
        assert outcome.offer is None
    finally:
        await engine.close()
