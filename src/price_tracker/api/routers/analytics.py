from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from price_tracker.api.schemas import (
    DashboardSummaryResponse,
    OfferAttentionResponse,
    OfferHistoryResponse,
    PricePointResponse,
    PriceStatsResponse,
    ProductComparisonResponse,
    RecentPriceChangeResponse,
)
from price_tracker.api.security import require_api_token
from price_tracker.services.analytics import AnalyticsService, OfferHistoryData, TimeRange

router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_api_token)],
    tags=["analytics"],
)


def _get_analytics(request: Request) -> AnalyticsService:
    svc: AnalyticsService = request.app.state.analytics_service
    return svc


def _map_offer_history(data: OfferHistoryData) -> OfferHistoryResponse:
    s = data.stats
    return OfferHistoryResponse(
        offer_id=data.offer_id,
        product_id=data.product_id,
        product_name=data.product_name,
        store_name=data.store_name,
        store_domain=data.store_domain,
        variant_label=data.variant_label,
        url=data.url,
        time_range=data.time_range,
        stats=PriceStatsResponse(
            points_count=s.points_count,
            currency=s.currency,
            current_price=s.current_price,
            previous_price=s.previous_price,
            min_price=s.min_price,
            max_price=s.max_price,
            avg_price=s.avg_price,
            last_change_amount=s.last_change_amount,
            last_change_percent=s.last_change_percent,
            period_change_amount=s.period_change_amount,
            period_change_percent=s.period_change_percent,
            lowest_recorded_at=s.lowest_recorded_at,
            highest_recorded_at=s.highest_recorded_at,
            first_recorded_at=s.first_recorded_at,
            last_recorded_at=s.last_recorded_at,
        ),
        points=[
            PricePointResponse(
                id=p.id,
                recorded_at=p.recorded_at,
                price=p.price,
                regular_price=p.regular_price,
                currency=p.currency,
                availability_status=p.availability_status,
                source_layer=p.source_layer,
            )
            for p in data.points
        ],
    )


@router.get("/offers/{offer_id}/history", response_model=OfferHistoryResponse)
async def get_offer_history(
    offer_id: int,
    analytics: Annotated[AnalyticsService, Depends(_get_analytics)],
    time_range: Annotated[TimeRange, Query(alias="range")] = TimeRange.D30,
) -> OfferHistoryResponse:
    data = await analytics.get_offer_history(offer_id, time_range=time_range)
    return _map_offer_history(data)


@router.get("/products/{product_id}/comparison", response_model=ProductComparisonResponse)
async def get_product_comparison(
    product_id: int,
    analytics: Annotated[AnalyticsService, Depends(_get_analytics)],
    time_range: Annotated[TimeRange, Query(alias="range")] = TimeRange.D30,
) -> ProductComparisonResponse:
    data = await analytics.get_product_comparison(product_id, time_range=time_range)
    return ProductComparisonResponse(
        product_id=data.product_id,
        product_name=data.product_name,
        time_range=data.time_range,
        lowest_current_offer_id=data.lowest_current_offer_id,
        offers=[_map_offer_history(item) for item in data.offers],
    )


@router.get("/dashboard", response_model=DashboardSummaryResponse)
async def get_dashboard(
    analytics: Annotated[AnalyticsService, Depends(_get_analytics)],
) -> DashboardSummaryResponse:
    summary = await analytics.get_dashboard_summary()
    return DashboardSummaryResponse(
        total_products=summary.total_products,
        total_offers=summary.total_offers,
        active_offers=summary.active_offers,
        paused_offers=summary.paused_offers,
        offers_with_errors=summary.offers_with_errors,
        out_of_stock_offers=summary.out_of_stock_offers,
        price_drops_24h=summary.price_drops_24h,
        price_rises_24h=summary.price_rises_24h,
        recent_changes=[
            RecentPriceChangeResponse(
                offer_id=item.offer_id,
                product_id=item.product_id,
                product_name=item.product_name,
                store_name=item.store_name,
                variant_label=item.variant_label,
                url=item.url,
                previous_price=item.previous_price,
                current_price=item.current_price,
                currency=item.currency,
                change_amount=item.change_amount,
                change_percent=item.change_percent,
                changed_at=item.changed_at,
            )
            for item in summary.recent_changes
        ],
        attention_items=[
            OfferAttentionResponse(
                offer_id=item.offer_id,
                product_id=item.product_id,
                product_name=item.product_name,
                store_name=item.store_name,
                variant_label=item.variant_label,
                availability_status=item.availability_status,
                consecutive_failures=item.consecutive_failures,
                last_error_code=item.last_error_code,
                last_error_message=item.last_error_message,
                last_checked_at=item.last_checked_at,
            )
            for item in summary.attention_items
        ],
    )
