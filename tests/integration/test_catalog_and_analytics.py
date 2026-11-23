from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from price_tracker.api.app import create_app
from price_tracker.config.settings import Settings
from price_tracker.core.enums import AvailabilityStatus, ExtractionLayer
from price_tracker.db.models import PriceHistory
from price_tracker.db.session import DatabaseManager
from price_tracker.scraping.engine import ScrapingEngine
from price_tracker.scraping.http_client import SafeHttpClient

XKOM_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Product",
  "name": "Sony WH-1000XM5 Czarne",
  "offers": {
    "@type": "Offer",
    "price": "1399.00",
    "priceCurrency": "PLN",
    "availability": "https://schema.org/InStock"
  }
}
</script>
</head><body><h1>Sony WH-1000XM5 Czarne</h1></body></html>
"""

OTHER_SHOP_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Product",
  "name": "Sony WH-1000XM5",
  "offers": {
    "@type": "Offer",
    "price": "1299.00",
    "priceCurrency": "PLN",
    "availability": "https://schema.org/InStock"
  }
}
</script>
</head><body><h1>Sony WH-1000XM5</h1></body></html>
"""


async def _public_dns(_host: str, _port: int) -> list[str]:
    return ["93.184.216.34"]


async def _build_mock_client(
    test_settings: Settings, db_manager: DatabaseManager
) -> tuple[httpx.AsyncClient, ScrapingEngine, dict[str, str]]:
    def handler(request: httpx.Request) -> httpx.Response:
        if "x-kom.pl" in request.url.host:
            return httpx.Response(200, text=XKOM_HTML)
        return httpx.Response(200, text=OTHER_SHOP_HTML)

    http_client = SafeHttpClient(resolver=_public_dns, transport=httpx.MockTransport(handler))
    engine = ScrapingEngine(http_client=http_client, enable_browser_fallback=False)
    app = create_app(
        settings=test_settings,
        db_manager=db_manager,
        scraping_engine=engine,
        start_scheduler=False,
    )
    headers = {"X-API-Token": test_settings.resolve_api_token()}
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )
    return client, engine, headers


async def test_full_product_lifecycle_multi_store_and_comparison(
    test_settings: Settings,
    db_manager: DatabaseManager,
) -> None:
    client, engine, headers = await _build_mock_client(test_settings, db_manager)
    try:
        async with client:
            # 1. Podgląd URL
            preview = await client.post(
                "/api/v1/urls/preview",
                headers=headers,
                json={"url": "https://www.x-kom.pl/p/sony-xm5.html"},
            )
            assert preview.status_code == 200
            prev_data = preview.json()
            assert prev_data["ok"] is True
            assert prev_data["title"] == "Sony WH-1000XM5 Czarne"
            assert Decimal(prev_data["current_price"]) == Decimal("1399.00")

            # 2. Kategoria + dodanie produktu z tagami
            cat_res = await client.post(
                "/api/v1/categories", headers=headers, json={"name": "Audio"}
            )
            assert cat_res.status_code == 201
            cat_id = cat_res.json()["id"]

            prod_res = await client.post(
                "/api/v1/products",
                headers=headers,
                json={
                    "url": "https://www.x-kom.pl/p/sony-xm5.html",
                    "category_id": cat_id,
                    "tags": ["słuchawki", "anc"],
                    "variant_label": "Czarny",
                },
            )
            assert prod_res.status_code == 201
            product = prod_res.json()
            product_id = product["id"]
            offer_1_id = product["offers"][0]["id"]
            assert product["name"] == "Sony WH-1000XM5 Czarne"
            assert product["tags"] == ["anc", "słuchawki"]

            # 3. Dodanie drugiego sklepu do tego samego produktu
            offer_2_res = await client.post(
                f"/api/v1/products/{product_id}/offers",
                headers=headers,
                json={
                    "url": "https://audio-shop.pl/sony-xm5",
                    "variant_label": "Czarny",
                },
            )
            assert offer_2_res.status_code == 201
            offer_2_id = offer_2_res.json()["id"]

            # 4. Duplikat tego samego URL i wariantu -> 409 Conflict
            dup_res = await client.post(
                f"/api/v1/products/{product_id}/offers",
                headers=headers,
                json={
                    "url": "https://audio-shop.pl/sony-xm5",
                    "variant_label": "Czarny",
                    "auto_fetch": False,
                },
            )
            assert dup_res.status_code == 409

            # 5. Filtrowanie po tagu i wyszukiwanie
            filtered = await client.get(
                "/api/v1/products",
                headers=headers,
                params={"tag": "anc", "search": "Sony"},
            )
            assert filtered.status_code == 200
            assert len(filtered.json()) == 1

            # 6. Ręczna korekta ceny oferty 1 -> zapis w historii jako MANUAL i aktualizacja zmian
            patch_res = await client.patch(
                f"/api/v1/offers/{offer_1_id}",
                headers=headers,
                json={"manual_price": "1349.00"},
            )
            assert patch_res.status_code == 200
            assert Decimal(patch_res.json()["current_price"]) == Decimal("1349.00")
            assert Decimal(patch_res.json()["previous_price"]) == Decimal("1399.00")

            # 7. Porównanie obu sklepów dla jednego produktu
            comp_res = await client.get(
                f"/api/v1/products/{product_id}/comparison",
                headers=headers,
                params={"range": "30d"},
            )
            assert comp_res.status_code == 200
            comp = comp_res.json()
            assert len(comp["offers"]) == 2
            # Oferta 2 (1299 zł) jest najtańsza
            assert comp["lowest_current_offer_id"] == offer_2_id

            # 8. Dashboard wykrywa spadek ceny w ostatnich 24h
            dash_res = await client.get("/api/v1/dashboard", headers=headers)
            assert dash_res.status_code == 200
            dash = dash_res.json()
            assert dash["total_products"] == 1
            assert dash["total_offers"] == 2
            assert dash["price_drops_24h"] == 1
            assert len(dash["recent_changes"]) == 1
            assert Decimal(dash["recent_changes"][0]["change_amount"]) == Decimal("-50.00")
    finally:
        await engine.close()


async def test_history_time_ranges_filter_only_real_points(
    test_settings: Settings,
    db_manager: DatabaseManager,
) -> None:
    client, engine, headers = await _build_mock_client(test_settings, db_manager)
    try:
        async with client:
            prod_res = await client.post(
                "/api/v1/products",
                headers=headers,
                json={
                    "url": "https://www.x-kom.pl/p/laptop-1.html",
                    "name": "Laptop Test",
                    "manual_price": "4000.00",
                    "manual_currency": "PLN",
                    "auto_fetch": False,
                },
            )
            assert prod_res.status_code == 201
            offer_id = prod_res.json()["offers"][0]["id"]

            # Dodajemy rzeczywiste pomiary z przeszłości: 120 dni temu i 10 dni temu
            now = datetime.now(UTC)
            async with db_manager.session() as session:
                session.add_all(
                    [
                        PriceHistory(
                            offer_id=offer_id,
                            recorded_at=now - timedelta(days=120),
                            price=Decimal("4800.00"),
                            currency="PLN",
                            availability_status=AvailabilityStatus.IN_STOCK,
                            source_layer=ExtractionLayer.JSON_LD,
                        ),
                        PriceHistory(
                            offer_id=offer_id,
                            recorded_at=now - timedelta(days=10),
                            price=Decimal("4300.00"),
                            currency="PLN",
                            availability_status=AvailabilityStatus.IN_STOCK,
                            source_layer=ExtractionLayer.JSON_LD,
                        ),
                    ]
                )

            # Zakres 7d -> tylko dzisiejszy punkt (4000.00)
            r7 = (
                await client.get(
                    f"/api/v1/offers/{offer_id}/history",
                    headers=headers,
                    params={"range": "7d"},
                )
            ).json()
            assert r7["stats"]["points_count"] == 1
            assert Decimal(r7["stats"]["min_price"]) == Decimal("4000.00")

            # Zakres 30d -> 2 punkty (4300.00, 4000.00)
            r30 = (
                await client.get(
                    f"/api/v1/offers/{offer_id}/history",
                    headers=headers,
                    params={"range": "30d"},
                )
            ).json()
            assert r30["stats"]["points_count"] == 2
            assert Decimal(r30["stats"]["max_price"]) == Decimal("4300.00")

            # Zakres 180d -> wszystkie 3 punkty (4800.00, 4300.00, 4000.00)
            r180 = (
                await client.get(
                    f"/api/v1/offers/{offer_id}/history",
                    headers=headers,
                    params={"range": "180d"},
                )
            ).json()
            assert r180["stats"]["points_count"] == 3
            assert Decimal(r180["stats"]["max_price"]) == Decimal("4800.00")
            assert Decimal(r180["stats"]["min_price"]) == Decimal("4000.00")

            # Zakres all -> również 3 punkty (bez żadnych sztucznych danych)
            r_all = (
                await client.get(
                    f"/api/v1/offers/{offer_id}/history",
                    headers=headers,
                    params={"range": "all"},
                )
            ).json()
            assert len(r_all["points"]) == 3
    finally:
        await engine.close()


async def test_monitoring_endpoints_stores_jobs_and_scheduler_control(
    test_settings: Settings,
    db_manager: DatabaseManager,
) -> None:
    client, engine, headers = await _build_mock_client(test_settings, db_manager)
    try:
        async with client:
            prod_res = await client.post(
                "/api/v1/products",
                headers=headers,
                json={"url": "https://www.x-kom.pl/p/monitor-99.html"},
            )
            offer_id = prod_res.json()["offers"][0]["id"]

            # Lista sklepów i aktualizacja limitu domeny
            stores = (await client.get("/api/v1/stores", headers=headers)).json()
            assert len(stores) == 1
            store_id = stores[0]["id"]
            assert stores[0]["adapter_key"] == "xkom"

            upd_store = await client.patch(
                f"/api/v1/stores/{store_id}",
                headers=headers,
                json={"rate_limit_seconds": 15, "requires_js": True},
            )
            assert upd_store.status_code == 200
            assert upd_store.json()["rate_limit_seconds"] == 15
            assert upd_store.json()["requires_js"] is True

            # Ręczne zakolejkowanie zadania
            enq = await client.post(f"/api/v1/offers/{offer_id}/check-now", headers=headers)
            assert enq.status_code == 202
            assert enq.json()["offer_id"] == offer_id

            jobs = (await client.get("/api/v1/jobs", headers=headers)).json()
            assert len(jobs) == 1
            assert jobs[0]["status"] == "pending"

            # Wstrzymanie i wznowienie harmonogramu
            paused = (await client.post("/api/v1/scheduler/pause", headers=headers)).json()
            assert paused["is_paused"] is True
            assert paused["pending_jobs"] == 1

            resumed = (await client.post("/api/v1/scheduler/resume", headers=headers)).json()
            assert resumed["is_paused"] is False
    finally:
        await engine.close()

async def test_delete_last_offer_also_removes_orphaned_product(
    test_settings: Settings,
    db_manager: DatabaseManager,
) -> None:
    client, engine, headers = await _build_mock_client(test_settings, db_manager)
    try:
        async with client:
            prod = (
                await client.post(
                    "/api/v1/products",
                    headers=headers,
                    json={
                        "url": "https://www.x-kom.pl/p/mysz-1.html",
                        "name": "Mysz bezprzewodowa",
                        "manual_price": "299.00",
                        "auto_fetch": False,
                    },
                )
            ).json()
            product_id = prod["id"]
            offer_1_id = prod["offers"][0]["id"]

            offer_2 = (
                await client.post(
                    f"/api/v1/products/{product_id}/offers",
                    headers=headers,
                    json={
                        "url": "https://other-shop.pl/mysz-1",
                        "manual_price": "289.00",
                        "auto_fetch": False,
                    },
                )
            ).json()
            offer_2_id = offer_2["id"]

            # Usunięcie 1 z 2 ofert zachowuje produkt
            del_1 = await client.delete(f"/api/v1/offers/{offer_1_id}", headers=headers)
            assert del_1.status_code == 204
            still_exists = await client.get(f"/api/v1/products/{product_id}", headers=headers)
            assert still_exists.status_code == 200
            assert len(still_exists.json()["offers"]) == 1

            # Usunięcie ostatniej oferty automatycznie usuwa osierocony produkt
            del_2 = await client.delete(f"/api/v1/offers/{offer_2_id}", headers=headers)
            assert del_2.status_code == 204
            gone = await client.get(f"/api/v1/products/{product_id}", headers=headers)
            assert gone.status_code == 404

            dash = (await client.get("/api/v1/dashboard", headers=headers)).json()
            assert dash["total_products"] == 0
            assert dash["total_offers"] == 0
    finally:
        await engine.close()
