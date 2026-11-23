from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from price_tracker.api.schemas import (
    CategoryCreateRequest,
    CategoryResponse,
    OfferCreateRequest,
    OfferResponse,
    OfferUpdateRequest,
    ProductCreateRequest,
    ProductResponse,
    ProductUpdateRequest,
    TagResponse,
    UrlPreviewRequest,
    UrlPreviewResponse,
)
from price_tracker.api.security import require_api_token
from price_tracker.core.enums import AvailabilityStatus
from price_tracker.db.models import Product, TrackedOffer
from price_tracker.services.catalog import CatalogService

router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_api_token)],
    tags=["catalog"],
)


def _get_catalog(request: Request) -> CatalogService:
    svc: CatalogService = request.app.state.catalog_service
    return svc


def _map_offer(offer: TrackedOffer) -> OfferResponse:
    return OfferResponse(
        id=offer.id,
        product_id=offer.product_id,
        store_id=offer.store_id,
        store_name=offer.store.name,
        store_domain=offer.store.domain,
        url=offer.url,
        variant_label=offer.variant_label,
        is_active=offer.is_active,
        check_interval_seconds=offer.check_interval_seconds,
        next_check_at=offer.next_check_at,
        last_checked_at=offer.last_checked_at,
        current_price=offer.current_price,
        regular_price=offer.regular_price,
        previous_price=offer.previous_price,
        currency=offer.currency,
        availability_status=offer.availability_status,
        last_price_change_at=offer.last_price_change_at,
        consecutive_failures=offer.consecutive_failures,
        last_error_code=offer.last_error_code,
        last_error_message=offer.last_error_message,
    )


def _map_product(product: Product) -> ProductResponse:
    category = (
        CategoryResponse(
            id=product.category.id,
            name=product.category.name,
            slug=product.category.slug,
        )
        if product.category is not None
        else None
    )
    tags = sorted(link.tag.name for link in product.tag_links)
    offers = [_map_offer(o) for o in sorted(product.offers, key=lambda x: x.id)]
    return ProductResponse(
        id=product.id,
        name=product.name,
        category=category,
        tags=tags,
        notes=product.notes,
        image_url=product.image_url,
        created_at=product.created_at,
        updated_at=product.updated_at,
        offers=offers,
    )


@router.post("/urls/preview", response_model=UrlPreviewResponse)
async def preview_url(
    payload: UrlPreviewRequest,
    catalog: Annotated[CatalogService, Depends(_get_catalog)],
) -> UrlPreviewResponse:
    outcome = await catalog.preview_url(payload.url, variant_label=payload.variant_label)
    offer = outcome.offer
    return UrlPreviewResponse(
        ok=outcome.ok,
        title=offer.title if offer else None,
        current_price=offer.current_price if offer else None,
        regular_price=offer.regular_price if offer else None,
        currency=offer.currency if offer else None,
        availability=offer.availability if offer else AvailabilityStatus.UNKNOWN,
        image_url=offer.image_url if offer else None,
        store_name=offer.store_name if offer else None,
        adapter_key=outcome.adapter_key,
        extraction_layer=outcome.extraction_layer,
        error_code=outcome.error_code,
        error_detail=outcome.error_detail,
    )


@router.get("/categories", response_model=list[CategoryResponse])
async def list_categories(
    catalog: Annotated[CatalogService, Depends(_get_catalog)],
) -> list[CategoryResponse]:
    cats = await catalog.list_categories()
    return [CategoryResponse(id=c.id, name=c.name, slug=c.slug) for c in cats]


@router.post(
    "/categories",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_category(
    payload: CategoryCreateRequest,
    catalog: Annotated[CatalogService, Depends(_get_catalog)],
) -> CategoryResponse:
    cat = await catalog.create_category(payload.name)
    return CategoryResponse(id=cat.id, name=cat.name, slug=cat.slug)


@router.get("/tags", response_model=list[TagResponse])
async def list_tags(
    catalog: Annotated[CatalogService, Depends(_get_catalog)],
) -> list[TagResponse]:
    tags = await catalog.list_tags()
    return [TagResponse(id=t.id, name=t.name) for t in tags]


@router.get("/products", response_model=list[ProductResponse])
async def list_products(
    catalog: Annotated[CatalogService, Depends(_get_catalog)],
    search: Annotated[str | None, Query()] = None,
    category_id: Annotated[int | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    store_id: Annotated[int | None, Query()] = None,
    availability: Annotated[AvailabilityStatus | None, Query()] = None,
    only_errors: Annotated[bool, Query()] = False,
    only_active: Annotated[bool, Query()] = False,
) -> list[ProductResponse]:
    items = await catalog.list_products(
        search=search,
        category_id=category_id,
        tag=tag,
        store_id=store_id,
        availability=availability,
        only_errors=only_errors,
        only_active=only_active,
    )
    return [_map_product(p) for p in items]


@router.post(
    "/products",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_product(
    payload: ProductCreateRequest,
    catalog: Annotated[CatalogService, Depends(_get_catalog)],
) -> ProductResponse:
    product = await catalog.create_product_with_offer(
        url=payload.url,
        name=payload.name,
        category_id=payload.category_id,
        tag_names=payload.tags,
        notes=payload.notes,
        image_url=payload.image_url,
        variant_label=payload.variant_label,
        check_interval_seconds=payload.check_interval_seconds,
        manual_price=payload.manual_price,
        manual_regular_price=payload.manual_regular_price,
        manual_currency=payload.manual_currency,
        manual_availability=payload.manual_availability,
        auto_fetch=payload.auto_fetch,
    )
    return _map_product(product)


@router.get("/products/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: int,
    catalog: Annotated[CatalogService, Depends(_get_catalog)],
) -> ProductResponse:
    product = await catalog.get_product(product_id)
    return _map_product(product)


@router.patch("/products/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: int,
    payload: ProductUpdateRequest,
    catalog: Annotated[CatalogService, Depends(_get_catalog)],
) -> ProductResponse:
    product = await catalog.update_product(
        product_id,
        name=payload.name,
        category_id=payload.category_id,
        clear_category=payload.clear_category,
        notes=payload.notes,
        image_url=payload.image_url,
        tag_names=payload.tags,
    )
    return _map_product(product)


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: int,
    catalog: Annotated[CatalogService, Depends(_get_catalog)],
) -> Response:
    await catalog.delete_product(product_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/products/{product_id}/offers",
    response_model=OfferResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_offer(
    product_id: int,
    payload: OfferCreateRequest,
    catalog: Annotated[CatalogService, Depends(_get_catalog)],
) -> OfferResponse:
    offer = await catalog.add_offer_to_product(
        product_id,
        url=payload.url,
        variant_label=payload.variant_label,
        check_interval_seconds=payload.check_interval_seconds,
        manual_price=payload.manual_price,
        manual_regular_price=payload.manual_regular_price,
        manual_currency=payload.manual_currency,
        manual_availability=payload.manual_availability,
        auto_fetch=payload.auto_fetch,
    )
    return _map_offer(offer)


@router.patch("/offers/{offer_id}", response_model=OfferResponse)
async def update_offer(
    offer_id: int,
    payload: OfferUpdateRequest,
    catalog: Annotated[CatalogService, Depends(_get_catalog)],
) -> OfferResponse:
    offer = await catalog.update_offer(
        offer_id,
        variant_label=payload.variant_label,
        is_active=payload.is_active,
        check_interval_seconds=payload.check_interval_seconds,
        use_default_interval=payload.use_default_interval,
        manual_price=payload.manual_price,
        manual_regular_price=payload.manual_regular_price,
        manual_currency=payload.manual_currency,
        manual_availability=payload.manual_availability,
    )
    return _map_offer(offer)


@router.delete("/offers/{offer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_offer(
    offer_id: int,
    catalog: Annotated[CatalogService, Depends(_get_catalog)],
) -> Response:
    await catalog.delete_offer(offer_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
