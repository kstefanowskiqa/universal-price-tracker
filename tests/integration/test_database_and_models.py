from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, StatementError

from price_tracker.core.enums import AlertRuleType, AvailabilityStatus, ExtractionLayer
from price_tracker.db.models import (
    AlertRule,
    Category,
    PriceHistory,
    Product,
    Store,
    TrackedOffer,
)
from price_tracker.db.session import DatabaseManager


async def test_sqlite_wal_and_foreign_keys_enabled(db_manager: DatabaseManager) -> None:
    async with db_manager.engine.connect() as conn:
        fk = (await conn.execute(text("PRAGMA foreign_keys"))).scalar()
        journal = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
    assert fk == 1
    assert str(journal).lower() == "wal"


async def test_money_decimal_precision_and_float_rejection(db_manager: DatabaseManager) -> None:
    async with db_manager.session() as session:
        store = Store(name="x-kom", domain="x-kom.pl")
        product = Product(name="Karta graficzna testowa")
        session.add_all([store, product])
        await session.flush()

        offer = TrackedOffer(
            product_id=product.id,
            store_id=store.id,
            url="https://www.x-kom.pl/p/12345-test.html",
            current_price=Decimal("2499.99"),
            regular_price=Decimal("2799.00"),
            currency="PLN",
            availability_status=AvailabilityStatus.IN_STOCK,
        )
        session.add(offer)
        await session.flush()

        history = PriceHistory(
            offer_id=offer.id,
            recorded_at=datetime(2026, 11, 22, 18, 0, tzinfo=UTC),
            price=Decimal("2499.99"),
            regular_price=Decimal("2799.00"),
            currency="PLN",
            availability_status=AvailabilityStatus.IN_STOCK,
            source_layer=ExtractionLayer.JSON_LD,
        )
        session.add(history)

    async with db_manager.session() as session:
        saved = (await session.execute(select(PriceHistory))).scalar_one()
        assert isinstance(saved.price, Decimal)
        assert saved.price == Decimal("2499.99")
        assert saved.regular_price == Decimal("2799.00")
        assert saved.recorded_at.tzinfo == UTC

    # Próba zapisu float musi zostać zablokowana
    with pytest.raises(StatementError):
        async with db_manager.session() as session:
            offer_id = (await session.execute(select(TrackedOffer.id))).scalar_one()
            bad = PriceHistory(
                offer_id=offer_id,
                recorded_at=datetime.now(UTC),
                price=199.99,  # type: ignore[arg-type]
                currency="PLN",
                availability_status=AvailabilityStatus.IN_STOCK,
                source_layer=ExtractionLayer.JSON_LD,
            )
            session.add(bad)


async def test_naive_datetime_rejected(db_manager: DatabaseManager) -> None:
    async with db_manager.session() as session:
        store = Store(name="H&M", domain="hm.com")
        product = Product(name="Kurtka")
        session.add_all([store, product])
        await session.flush()
        offer = TrackedOffer(
            product_id=product.id,
            store_id=store.id,
            url="https://www2.hm.com/pl_pl/productpage.12345.html",
        )
        session.add(offer)
        await session.flush()
        offer_id = offer.id

    with pytest.raises(StatementError):
        async with db_manager.session() as session:
            session.add(
                PriceHistory(
                    offer_id=offer_id,
                    recorded_at=datetime(2026, 11, 22, 12, 0),  # naiwna data bez tzinfo
                    price=Decimal("199.99"),
                    currency="PLN",
                )
            )


async def test_offer_url_variant_uniqueness_and_alert_constraint(
    db_manager: DatabaseManager,
) -> None:
    async with db_manager.session() as session:
        cat = Category(name="Elektronika", slug="elektronika")
        store = Store(name="Victoria's Secret", domain="victoriassecret.com")
        product = Product(name="Perfumy", category=cat)
        session.add_all([cat, store, product])
        await session.flush()

        # Dwa różne warianty pod tym samym URL są dozwolone
        offer_50 = TrackedOffer(
            product_id=product.id,
            store_id=store.id,
            url="https://www.victoriassecret.com/p/1",
            variant_label="50ml",
        )
        offer_100 = TrackedOffer(
            product_id=product.id,
            store_id=store.id,
            url="https://www.victoriassecret.com/p/1",
            variant_label="100ml",
        )
        session.add_all([offer_50, offer_100])
        await session.flush()

        # Reguła alertu z celem działa poprawnie
        session.add(
            AlertRule(
                offer_id=offer_50.id,
                rule_type=AlertRuleType.TARGET_PRICE_BELOW,
                threshold_value=Decimal("250.00"),
            )
        )

    # Duplikat tego samego URL i wariantu musi rzucić IntegrityError
    with pytest.raises(IntegrityError):
        async with db_manager.session() as session:
            store_id = (await session.execute(select(Store.id))).scalar_one()
            product_id = (await session.execute(select(Product.id))).scalar_one()
            session.add(
                TrackedOffer(
                    product_id=product_id,
                    store_id=store_id,
                    url="https://www.victoriassecret.com/p/1",
                    variant_label="50ml",
                )
            )

    # Reguła alertu bez offer_id i bez product_id musi rzucić IntegrityError (CheckConstraint)
    with pytest.raises(IntegrityError):
        async with db_manager.session() as session:
            session.add(
                AlertRule(
                    offer_id=None,
                    product_id=None,
                    rule_type=AlertRuleType.PRICE_DROP,
                )
            )
