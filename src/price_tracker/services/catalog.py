from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from price_tracker.core.enums import AvailabilityStatus, ErrorCode, ExtractionLayer
from price_tracker.core.exceptions import ConflictError, NotFoundError, ValidationError
from price_tracker.core.ssrf import extract_domain, normalize_url
from price_tracker.db.models import (
    Category,
    CheckJob,
    PriceHistory,
    Product,
    ProductTag,
    Store,
    Tag,
    TrackedOffer,
)
from price_tracker.db.session import DatabaseManager
from price_tracker.scraping.engine import ScrapingEngine
from price_tracker.scraping.models import ScrapeOutcome
from price_tracker.scraping.parsers.price_parser import normalize_currency

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_RE.sub("-", normalized.lower()).strip("-")
    return slug or "kategoria"

@dataclass(frozen=True, slots=True)
class StoreHealthView:
    store: Store
    offers_count: int
    active_offers_count: int
    failing_offers_count: int
    success_rate_percent: Decimal | None


class CatalogService:
    """Zarządza produktami, ofertami (sklepy/warianty), kategoriami, tagami i sklepami."""

    def __init__(
        self,
        db_manager: DatabaseManager,
        scraping_engine: ScrapingEngine,
        *,
        default_check_interval_seconds: int = 3600,
    ) -> None:
        self._db = db_manager
        self._engine = scraping_engine
        self._default_interval = default_check_interval_seconds

    def set_default_interval(self, seconds: int) -> None:
        self._default_interval = seconds

    async def preview_url(self, raw_url: str, *, variant_label: str = "") -> ScrapeOutcome:
        return await self._engine.preview_url(raw_url, variant_label=variant_label)

    # ---- Kategorie i tagi ----

    async def list_categories(self) -> list[Category]:
        async with self._db.session() as session:
            stmt = select(Category).order_by(Category.name.asc())
            return list((await session.execute(stmt)).scalars().all())

    async def create_category(self, name: str) -> Category:
        clean = name.strip()
        if not clean:
            raise ValidationError("Nazwa kategorii nie może być pusta.")
        async with self._db.session() as session:
            category = Category(name=clean, slug=slugify(clean))
            session.add(category)
            try:
                await session.flush()
            except IntegrityError as exc:
                raise ConflictError(f"Kategoria '{clean}' już istnieje.") from exc
            return category

    async def list_tags(self) -> list[Tag]:
        async with self._db.session() as session:
            stmt = select(Tag).order_by(Tag.name.asc())
            return list((await session.execute(stmt)).scalars().all())

    async def _sync_product_tags(
        self,
        session: AsyncSession,
        product: Product,
        tag_names: list[str],
    ) -> None:
        cleaned = list(dict.fromkeys(t.strip().lower() for t in tag_names if t.strip()))
        await session.execute(delete(ProductTag).where(ProductTag.product_id == product.id))
        await session.flush()

        for tag_name in cleaned:
            tag = (
                await session.execute(select(Tag).where(Tag.name == tag_name))
            ).scalar_one_or_none()
            if tag is None:
                tag = Tag(name=tag_name)
                session.add(tag)
                await session.flush()
            session.add(ProductTag(product_id=product.id, tag_id=tag.id))

    async def _get_or_create_store(
        self,
        session: AsyncSession,
        url: str,
        *,
        preferred_name: str | None = None,
    ) -> Store:
        domain = extract_domain(url)
        if not domain:
            raise ValidationError("Nie udało się wyznaczyć domeny z adresu URL.")

        store = (
            await session.execute(select(Store).where(Store.domain == domain))
        ).scalar_one_or_none()
        if store is not None:
            return store

        adapter = self._engine.registry.resolve(domain)
        display_name = preferred_name or (
            adapter.display_name if adapter.adapter_key != "universal" else domain
        )
        store = Store(
            name=display_name,
            domain=domain,
            adapter_key=adapter.adapter_key,
            requires_js=adapter.prefer_js,
        )
        session.add(store)
        await session.flush()
        return store

    # ---- Produkty i oferty ----

    async def create_product_with_offer(
        self,
        *,
        url: str,
        name: str | None = None,
        category_id: int | None = None,
        tag_names: list[str] | None = None,
        notes: str | None = None,
        image_url: str | None = None,
        variant_label: str = "",
        check_interval_seconds: int | None = None,
        manual_price: Decimal | None = None,
        manual_regular_price: Decimal | None = None,
        manual_currency: str | None = None,
        manual_availability: AvailabilityStatus | None = None,
        auto_fetch: bool = True,
    ) -> Product:
        normalized_url = normalize_url(url)
        domain = extract_domain(normalized_url)

        outcome: ScrapeOutcome | None = None
        if auto_fetch:
            outcome = await self._engine.preview_url(
                normalized_url, variant_label=variant_label.strip()
            )

        extracted = outcome.offer if (outcome and outcome.ok and outcome.offer) else None

        resolved_name = (
            (name or "").strip()
            or (extracted.title if extracted and extracted.title else "")
            or f"Produkt ({domain})"
        )
        resolved_image = (image_url or "").strip() or (
            extracted.image_url if extracted else None
        )
        resolved_price = (
            manual_price
            if manual_price is not None
            else (extracted.current_price if extracted else None)
        )
        resolved_regular = (
            manual_regular_price
            if manual_regular_price is not None
            else (extracted.regular_price if extracted else None)
        )
        resolved_currency = (
            normalize_currency(manual_currency)
            or (extracted.currency if extracted else None)
            or ("PLN" if resolved_price is not None else None)
        )
        resolved_availability = (
            manual_availability
            if manual_availability is not None
            else (extracted.availability if extracted else AvailabilityStatus.UNKNOWN)
        )

        if manual_price is not None and manual_price <= 0:
            raise ValidationError("Cena ręczna musi być większa od zera.")

        now = datetime.now(UTC)
        interval = check_interval_seconds or self._default_interval

        async with self._db.session() as session:
            if category_id is not None:
                cat = await session.get(Category, category_id)
                if cat is None:
                    raise NotFoundError(f"Kategoria o id={category_id} nie istnieje.")

            store = await self._get_or_create_store(
                session,
                normalized_url,
                preferred_name=extracted.store_name if extracted else None,
            )

            product = Product(
                name=resolved_name,
                category_id=category_id,
                notes=notes,
                image_url=resolved_image,
            )
            session.add(product)
            await session.flush()

            if tag_names:
                await self._sync_product_tags(session, product, tag_names)

            has_fetch_error = bool(outcome and not outcome.ok and manual_price is None)
            checked_at = now if (outcome is not None or resolved_price is not None) else None
            err_code = outcome.error_code if (has_fetch_error and outcome) else ErrorCode.NONE
            err_msg = outcome.error_detail if (has_fetch_error and outcome) else None

            offer = TrackedOffer(
                product_id=product.id,
                store_id=store.id,
                url=normalized_url,
                variant_label=variant_label.strip(),
                is_active=True,
                check_interval_seconds=check_interval_seconds,
                next_check_at=now + timedelta(seconds=interval) if resolved_price else now,
                last_checked_at=checked_at,
                current_price=resolved_price,
                regular_price=resolved_regular,
                currency=resolved_currency,
                availability_status=resolved_availability,
                consecutive_failures=1 if has_fetch_error else 0,
                last_error_code=err_code,
                last_error_message=err_msg,
            )
            session.add(offer)
            try:
                await session.flush()
            except IntegrityError as exc:
                raise ConflictError(
                    "Ta oferta (URL i wariant) jest już monitorowana w bazie danych."
                ) from exc

            if resolved_price is not None and resolved_currency is not None:
                source_layer = (
                    ExtractionLayer.MANUAL
                    if manual_price is not None
                    else (extracted.extraction_layer if extracted else ExtractionLayer.MANUAL)
                )
                session.add(
                    PriceHistory(
                        offer_id=offer.id,
                        recorded_at=now,
                        price=resolved_price,
                        regular_price=resolved_regular,
                        currency=resolved_currency,
                        availability_status=resolved_availability,
                        source_layer=source_layer,
                    )
                )

            product_id = product.id

        return await self.get_product(product_id)

    async def add_offer_to_product(
        self,
        product_id: int,
        *,
        url: str,
        variant_label: str = "",
        check_interval_seconds: int | None = None,
        manual_price: Decimal | None = None,
        manual_regular_price: Decimal | None = None,
        manual_currency: str | None = None,
        manual_availability: AvailabilityStatus | None = None,
        auto_fetch: bool = True,
    ) -> TrackedOffer:
        normalized_url = normalize_url(url)
        if manual_price is not None and manual_price <= 0:
            raise ValidationError("Cena ręczna musi być większa od zera.")

        outcome: ScrapeOutcome | None = None
        if auto_fetch:
            outcome = await self._engine.preview_url(
                normalized_url, variant_label=variant_label.strip()
            )
        extracted = outcome.offer if (outcome and outcome.ok and outcome.offer) else None

        resolved_price = (
            manual_price
            if manual_price is not None
            else (extracted.current_price if extracted else None)
        )
        resolved_regular = (
            manual_regular_price
            if manual_regular_price is not None
            else (extracted.regular_price if extracted else None)
        )
        resolved_currency = (
            normalize_currency(manual_currency)
            or (extracted.currency if extracted else None)
            or ("PLN" if resolved_price is not None else None)
        )
        resolved_availability = (
            manual_availability
            if manual_availability is not None
            else (extracted.availability if extracted else AvailabilityStatus.UNKNOWN)
        )

        now = datetime.now(UTC)
        interval = check_interval_seconds or self._default_interval

        async with self._db.session() as session:
            product = await session.get(Product, product_id)
            if product is None:
                raise NotFoundError(f"Produkt o id={product_id} nie istnieje.")

            store = await self._get_or_create_store(
                session,
                normalized_url,
                preferred_name=extracted.store_name if extracted else None,
            )

            has_fetch_error = bool(outcome and not outcome.ok and manual_price is None)
            checked_at = now if (outcome is not None or resolved_price is not None) else None
            err_code = outcome.error_code if (has_fetch_error and outcome) else ErrorCode.NONE
            err_msg = outcome.error_detail if (has_fetch_error and outcome) else None

            offer = TrackedOffer(
                product_id=product.id,
                store_id=store.id,
                url=normalized_url,
                variant_label=variant_label.strip(),
                is_active=True,
                check_interval_seconds=check_interval_seconds,
                next_check_at=now + timedelta(seconds=interval) if resolved_price else now,
                last_checked_at=checked_at,
                current_price=resolved_price,
                regular_price=resolved_regular,
                currency=resolved_currency,
                availability_status=resolved_availability,
                consecutive_failures=1 if has_fetch_error else 0,
                last_error_code=err_code,
                last_error_message=err_msg,
            )
            session.add(offer)
            try:
                await session.flush()
            except IntegrityError as exc:
                raise ConflictError(
                    "Ta oferta (URL i wariant) jest już monitorowana w bazie danych."
                ) from exc

            if resolved_price is not None and resolved_currency is not None:
                source_layer = (
                    ExtractionLayer.MANUAL
                    if manual_price is not None
                    else (extracted.extraction_layer if extracted else ExtractionLayer.MANUAL)
                )
                session.add(
                    PriceHistory(
                        offer_id=offer.id,
                        recorded_at=now,
                        price=resolved_price,
                        regular_price=resolved_regular,
                        currency=resolved_currency,
                        availability_status=resolved_availability,
                        source_layer=source_layer,
                    )
                )
            offer_id = offer.id

        return await self.get_offer(offer_id)

    async def get_product(self, product_id: int) -> Product:
        async with self._db.session() as session:
            stmt = (
                select(Product)
                .options(
                    selectinload(Product.category),
                    selectinload(Product.tag_links).selectinload(ProductTag.tag),
                    selectinload(Product.offers).selectinload(TrackedOffer.store),
                )
                .where(Product.id == product_id)
            )
            product = (await session.execute(stmt)).scalar_one_or_none()
            if product is None:
                raise NotFoundError(f"Produkt o id={product_id} nie istnieje.")
            return product

    async def get_offer(self, offer_id: int) -> TrackedOffer:
        async with self._db.session() as session:
            stmt = (
                select(TrackedOffer)
                .options(
                    selectinload(TrackedOffer.store),
                    selectinload(TrackedOffer.product),
                )
                .where(TrackedOffer.id == offer_id)
            )
            offer = (await session.execute(stmt)).scalar_one_or_none()
            if offer is None:
                raise NotFoundError(f"Oferta o id={offer_id} nie istnieje.")
            return offer

    async def list_products(
        self,
        *,
        search: str | None = None,
        category_id: int | None = None,
        tag: str | None = None,
        store_id: int | None = None,
        availability: AvailabilityStatus | None = None,
        only_errors: bool = False,
        only_active: bool = False,
    ) -> list[Product]:
        async with self._db.session() as session:
            stmt = (
                select(Product)
                .options(
                    selectinload(Product.category),
                    selectinload(Product.tag_links).selectinload(ProductTag.tag),
                    selectinload(Product.offers).selectinload(TrackedOffer.store),
                )
                .order_by(Product.updated_at.desc(), Product.id.desc())
            )
            if category_id is not None:
                stmt = stmt.where(Product.category_id == category_id)
            if search and search.strip():
                pattern = f"%{search.strip()}%"
                stmt = stmt.where(Product.name.ilike(pattern))

            products = list((await session.execute(stmt)).scalars().unique().all())

        filtered: list[Product] = []
        tag_needle = tag.strip().lower() if tag and tag.strip() else None

        for product in products:
            if tag_needle is not None:
                p_tags = {link.tag.name.lower() for link in product.tag_links}
                if tag_needle not in p_tags:
                    continue

            if any(
                [
                    store_id is not None,
                    availability is not None,
                    only_errors,
                    only_active,
                ]
            ):
                matching_offers = [
                    o
                    for o in product.offers
                    if (store_id is None or o.store_id == store_id)
                    and (availability is None or o.availability_status == availability)
                    and (not only_errors or o.consecutive_failures > 0)
                    and (not only_active or o.is_active)
                ]
                if not matching_offers:
                    continue

            filtered.append(product)

        return filtered

    async def update_product(
        self,
        product_id: int,
        *,
        name: str | None = None,
        category_id: int | None = None,
        clear_category: bool = False,
        notes: str | None = None,
        image_url: str | None = None,
        tag_names: list[str] | None = None,
    ) -> Product:
        async with self._db.session() as session:
            product = await session.get(Product, product_id)
            if product is None:
                raise NotFoundError(f"Produkt o id={product_id} nie istnieje.")

            if name is not None:
                clean = name.strip()
                if not clean:
                    raise ValidationError("Nazwa produktu nie może być pusta.")
                product.name = clean

            if clear_category:
                product.category_id = None
            elif category_id is not None:
                cat = await session.get(Category, category_id)
                if cat is None:
                    raise NotFoundError(f"Kategoria o id={category_id} nie istnieje.")
                product.category_id = category_id

            if notes is not None:
                product.notes = notes
            if image_url is not None:
                product.image_url = image_url.strip() or None
            if tag_names is not None:
                await self._sync_product_tags(session, product, tag_names)

        return await self.get_product(product_id)

    async def delete_product(self, product_id: int) -> None:
        async with self._db.session() as session:
            product = await session.get(Product, product_id)
            if product is None:
                raise NotFoundError(f"Produkt o id={product_id} nie istnieje.")
            await session.delete(product)

    async def update_offer(
        self,
        offer_id: int,
        *,
        variant_label: str | None = None,
        is_active: bool | None = None,
        check_interval_seconds: int | None = None,
        use_default_interval: bool = False,
        manual_price: Decimal | None = None,
        manual_regular_price: Decimal | None = None,
        manual_currency: str | None = None,
        manual_availability: AvailabilityStatus | None = None,
    ) -> TrackedOffer:
        if manual_price is not None and manual_price <= 0:
            raise ValidationError("Cena musi być większa od zera.")

        now = datetime.now(UTC)

        async with self._db.session() as session:
            offer = await session.get(TrackedOffer, offer_id)
            if offer is None:
                raise NotFoundError(f"Oferta o id={offer_id} nie istnieje.")

            if variant_label is not None:
                offer.variant_label = variant_label.strip()
            if is_active is not None:
                offer.is_active = is_active
                if is_active and offer.next_check_at is None:
                    offer.next_check_at = now
            if use_default_interval:
                offer.check_interval_seconds = None
            elif check_interval_seconds is not None:
                offer.check_interval_seconds = check_interval_seconds

            if manual_currency is not None:
                norm_curr = normalize_currency(manual_currency)
                if norm_curr is None:
                    raise ValidationError(f"Nieprawidłowy kod waluty: '{manual_currency}'.")
                offer.currency = norm_curr

            if manual_availability is not None:
                offer.availability_status = manual_availability

            if manual_price is not None:
                if offer.current_price is not None and offer.current_price != manual_price:
                    offer.previous_price = offer.current_price
                    offer.last_price_change_at = now
                offer.current_price = manual_price
                if manual_regular_price is not None:
                    offer.regular_price = manual_regular_price
                offer.currency = offer.currency or "PLN"
                offer.last_checked_at = now
                offer.consecutive_failures = 0
                offer.last_error_code = ErrorCode.NONE
                offer.last_error_message = None

                session.add(
                    PriceHistory(
                        offer_id=offer.id,
                        recorded_at=now,
                        price=manual_price,
                        regular_price=offer.regular_price,
                        currency=offer.currency,
                        availability_status=offer.availability_status,
                        source_layer=ExtractionLayer.MANUAL,
                    )
                )

            try:
                await session.flush()
            except IntegrityError as exc:
                raise ConflictError(
                    "Istnieje już oferta z tym samym adresem URL i wariantem."
                ) from exc

        return await self.get_offer(offer_id)

    async def delete_offer(self, offer_id: int) -> None:
        async with self._db.session() as session:
            offer = await session.get(TrackedOffer, offer_id)
            if offer is None:
                raise NotFoundError(f"Oferta o id={offer_id} nie istnieje.")
            product_id = offer.product_id
            await session.delete(offer)
            await session.flush()

            remaining_count = int(
                (
                    await session.execute(
                        select(func.count(TrackedOffer.id)).where(
                            TrackedOffer.product_id == product_id
                        )
                    )
                ).scalar_one()
            )
            if remaining_count == 0:
                product = await session.get(Product, product_id)
                if product is not None:
                    await session.delete(product)

    # ---- Sklepy i kolejka zadań ----

    async def list_stores_with_health(self) -> list[StoreHealthView]:
        async with self._db.session() as session:
            stores = list(
                (
                    await session.execute(
                        select(Store)
                        .options(selectinload(Store.offers))
                        .order_by(Store.name.asc())
                    )
                )
                .scalars()
                .all()
            )
            job_rows = (
                await session.execute(
                    select(
                        TrackedOffer.store_id,
                        CheckJob.status,
                        func.count(CheckJob.id),
                    )
                    .join(TrackedOffer, CheckJob.offer_id == TrackedOffer.id)
                    .group_by(TrackedOffer.store_id, CheckJob.status)
                )
            ).all()

        totals: dict[int, int] = {}
        successes: dict[int, int] = {}
        for store_id, status, cnt in job_rows:
            if str(status) in {"success", "failed"}:
                totals[store_id] = totals.get(store_id, 0) + int(cnt)
                if str(status) == "success":
                    successes[store_id] = successes.get(store_id, 0) + int(cnt)

        views: list[StoreHealthView] = []
        for store in stores:
            total_runs = totals.get(store.id, 0)
            rate: Decimal | None = None
            if total_runs > 0:
                rate = (
                    (Decimal(successes.get(store.id, 0)) / Decimal(total_runs)) * Decimal("100")
                ).quantize(Decimal("0.1"))
            failing_cnt = sum(1 for o in store.offers if o.consecutive_failures > 0)
            views.append(
                StoreHealthView(
                    store=store,
                    offers_count=len(store.offers),
                    active_offers_count=sum(1 for o in store.offers if o.is_active),
                    failing_offers_count=failing_cnt,
                    success_rate_percent=rate,
                )
            )
        return views

    async def update_store(
        self,
        store_id: int,
        *,
        name: str | None = None,
        adapter_key: str | None = None,
        rate_limit_seconds: int | None = None,
        requires_js: bool | None = None,
        custom_selectors_json: dict[str, Any] | None = None,
        clear_custom_selectors: bool = False,
        is_enabled: bool | None = None,
    ) -> Store:
        async with self._db.session() as session:
            store = await session.get(Store, store_id)
            if store is None:
                raise NotFoundError(f"Sklep o id={store_id} nie istnieje.")

            if name is not None and name.strip():
                store.name = name.strip()
            if adapter_key is not None:
                available = {a.adapter_key for a in self._engine.registry.list_adapters()}
                if adapter_key not in available:
                    raise ValidationError(
                        f"Nieznany adapter '{adapter_key}'. Dostępne: {sorted(available)}."
                    )
                store.adapter_key = adapter_key
            if rate_limit_seconds is not None:
                if rate_limit_seconds < 0 or rate_limit_seconds > 3600:
                    raise ValidationError("Limit zapytań sklepu musi mieścić się w 0..3600s.")
                store.rate_limit_seconds = rate_limit_seconds
            if requires_js is not None:
                store.requires_js = requires_js
            if clear_custom_selectors:
                store.custom_selectors_json = None
            elif custom_selectors_json is not None:
                store.custom_selectors_json = custom_selectors_json
            if is_enabled is not None:
                store.is_enabled = is_enabled

            await session.flush()
            return store

    async def list_check_jobs(
        self,
        *,
        offer_id: int | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[CheckJob]:
        async with self._db.session() as session:
            stmt = (
                select(CheckJob)
                .options(
                    selectinload(CheckJob.offer).selectinload(TrackedOffer.product),
                    selectinload(CheckJob.offer).selectinload(TrackedOffer.store),
                )
                .order_by(CheckJob.scheduled_at.desc(), CheckJob.id.desc())
                .limit(min(max(limit, 1), 200))
            )
            if offer_id is not None:
                stmt = stmt.where(CheckJob.offer_id == offer_id)
            if status:
                stmt = stmt.where(CheckJob.status == status)
            return list((await session.execute(stmt)).scalars().all())
