from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from price_tracker.api.app import create_app
from price_tracker.config.secrets import SecretStore
from price_tracker.config.settings import Settings
from price_tracker.core.enums import AlertRuleType, NotificationDeliveryStatus
from price_tracker.db.models import Product, Store, TrackedOffer
from price_tracker.db.session import DatabaseManager
from price_tracker.notifications.channels import build_default_channel_registry
from price_tracker.notifications.service import NotificationService
from price_tracker.scheduler.domain_limiter import DomainRateLimiter
from price_tracker.scheduler.worker import MonitoringScheduler
from price_tracker.scraping.engine import ScrapingEngine
from price_tracker.scraping.http_client import SafeHttpClient


class InMemorySecretStore(SecretStore):
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get_secret(self, key: str) -> str | None:
        return self._data.get(key)

    def set_secret(self, key: str, value: str) -> None:
        self._data[key] = value

    def delete_secret(self, key: str) -> None:
        self._data.pop(key, None)


async def _public_dns(_host: str, _port: int) -> list[str]:
    return ["93.184.216.34"]


def _html_with_price(price: str) -> str:
    return f"""
    <html><head>
    <script type="application/ld+json">
    {{
      "@context": "https://schema.org",
      "@type": "Product",
      "name": "Monitor 4K",
      "offers": {{
        "@type": "Offer",
        "price": "{price}",
        "priceCurrency": "PLN",
        "availability": "https://schema.org/InStock"
      }}
    }}
    </script>
    </head><body><h1>Monitor 4K</h1></body></html>
    """


async def test_scheduler_to_telegram_notification_without_duplicates(
    db_manager: DatabaseManager,
) -> None:
    prices = iter(["2500.00", "2150.00", "2150.00"])
    sent_messages: list[str] = []

    def shop_handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_html_with_price(next(prices)))

    def telegram_handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode("utf-8"))
        sent_messages.append(str(body["text"]))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent_messages)}})

    secrets = InMemorySecretStore()
    registry = build_default_channel_registry(
        telegram_transport=httpx.MockTransport(telegram_handler)
    )
    notif_service = NotificationService(
        db_manager,
        secret_store=secrets,
        channel_registry=registry,
    )
    http_client = SafeHttpClient(resolver=_public_dns, transport=httpx.MockTransport(shop_handler))
    engine = ScrapingEngine(http_client=http_client, enable_browser_fallback=False)
    limiter = DomainRateLimiter(default_domain_delay_seconds=0.0, jitter_ratio=0.0)
    scheduler = MonitoringScheduler(
        db_manager,
        engine,
        rate_limiter=limiter,
        on_check_completed=notif_service.handle_price_check_event,
    )

    try:
        await notif_service.configure_telegram_channel(
            chat_id="555000",
            bot_token="123456789:AAH_test_bot_token_1234567890abcd",
        )

        async with db_manager.session() as session:
            store = Store(name="x-kom", domain="x-kom.pl", adapter_key="xkom", rate_limit_seconds=0)
            product = Product(name="Monitor 4K")
            session.add_all([store, product])
            await session.flush()

            offer = TrackedOffer(
                product_id=product.id,
                store_id=store.id,
                url="https://www.x-kom.pl/p/monitor-4k.html",
                next_check_at=datetime.now(UTC) - timedelta(minutes=1),
            )
            session.add(offer)
            await session.flush()
            offer_id = offer.id

        # Reguła: powiadom, gdy cena <= 2200 PLN
        await notif_service.create_alert_rule(
            offer_id=offer_id,
            rule_type=AlertRuleType.TARGET_PRICE_BELOW,
            threshold_value=Decimal("2200.00"),
            cooldown_minutes=0,
        )

        # 1. Sprawdzenie: 2500.00 -> brak alertu
        await scheduler.enqueue_manual_check(offer_id)
        await scheduler.run_pending_batch()
        assert len(sent_messages) == 0

        # 2. Sprawdzenie: 2150.00 -> 1 alert na Telegramie!
        await scheduler.enqueue_manual_check(offer_id)
        await scheduler.run_pending_batch()
        assert len(sent_messages) == 1
        assert "2500.00 PLN" in sent_messages[0]
        assert "2150.00 PLN" in sent_messages[0]
        assert "-350.00 PLN (-14.00%)" in sent_messages[0]

        # 3. Sprawdzenie: nadal 2150.00 -> zero duplikatów!
        await scheduler.enqueue_manual_check(offer_id)
        await scheduler.run_pending_batch()
        assert len(sent_messages) == 1

        logs = await notif_service.list_notification_logs(offer_id=offer_id)
        assert len(logs) == 1
        assert logs[0].status == NotificationDeliveryStatus.SENT
    finally:
        await notif_service.close()
        await engine.close()


async def test_notifications_api_endpoints(
    test_settings: Settings,
    db_manager: DatabaseManager,
) -> None:
    sent_count = {"n": 0}

    def telegram_handler(_req: httpx.Request) -> httpx.Response:
        sent_count["n"] += 1
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    secrets = InMemorySecretStore()
    registry = build_default_channel_registry(
        telegram_transport=httpx.MockTransport(telegram_handler)
    )
    notif_service = NotificationService(
        db_manager,
        secret_store=secrets,
        channel_registry=registry,
    )
    http_client = SafeHttpClient(
        resolver=_public_dns,
        transport=httpx.MockTransport
        (lambda _r: httpx.Response(200, text=_html_with_price("999.00"))),
    )
    engine = ScrapingEngine(http_client=http_client, enable_browser_fallback=False)
    app = create_app(
        settings=test_settings,
        db_manager=db_manager,
        scraping_engine=engine,
        notification_service=notif_service,
        start_scheduler=False,
    )
    headers = {"X-API-Token": test_settings.resolve_api_token()}

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            # 1. Konfiguracja Telegrama (token trafia do SecretStore i NIE wraca w JSON!)
            cfg_res = await client.put(
                "/api/v1/notifications/channels/telegram",
                headers=headers,
                json={
                    "chat_id": "12345678",
                    "bot_token": "111222333:AAH_api_test_secret_token_1234567890",
                },
            )
            assert cfg_res.status_code == 200
            body = cfg_res.json()
            assert body["has_secret"] is True
            assert "AAH_api_test_secret_token" not in json.dumps(body)

            # 2. Test połączenia Telegrama
            test_res = await client.post(
                "/api/v1/notifications/channels/telegram/test",
                headers=headers,
            )
            assert test_res.status_code == 200
            assert test_res.json()["ok"] is True
            assert sent_count["n"] == 1

            # 3. Dodanie produktu i reguły alertu
            prod = (
                await client.post(
                    "/api/v1/products",
                    headers=headers,
                    json={
                        "url": "https://www.x-kom.pl/p/klawiatura.html",
                        "name": "Klawiatura",
                        "manual_price": "400.00",
                        "auto_fetch": False,
                    },
                )
            ).json()
            offer_id = prod["offers"][0]["id"]

            rule_res = await client.post(
                "/api/v1/alerts",
                headers=headers,
                json={
                    "offer_id": offer_id,
                    "rule_type": "target_price_below",
                    "threshold_value": "350.00",
                    "cooldown_minutes": 30,
                },
            )
            assert rule_res.status_code == 201
            rule_id = rule_res.json()["id"]

            # 4. Edycja i odczyt reguł + historia logów powiadomień
            upd = await client.patch(
                f"/api/v1/alerts/{rule_id}",
                headers=headers,
                json={"is_enabled": False},
            )
            assert upd.status_code == 200
            assert upd.json()["is_enabled"] is False

            logs = (await client.get("/api/v1/notifications/logs", headers=headers)).json()
            assert len(logs) == 1
            assert logs[0]["status"] == "sent"

            # 5. Usunięcie reguły
            del_res = await client.delete(f"/api/v1/alerts/{rule_id}", headers=headers)
            assert del_res.status_code == 204
    finally:
        await notif_service.close()
        await engine.close()
