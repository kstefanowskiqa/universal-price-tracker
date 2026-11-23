from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from price_tracker.core.enums import (
    AlertRuleType,
    AvailabilityStatus,
    CheckJobStatus,
    CheckTriggerType,
    ErrorCode,
    ExtractionLayer,
    NotificationChannelType,
    NotificationDeliveryStatus,
)
from price_tracker.services.analytics import TimeRange


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: Literal["ok", "error"]
    timestamp_utc: datetime


class SystemInfoResponse(BaseModel):
    version: str
    environment: str
    database_dialect: Literal["sqlite", "postgresql", "other"]
    default_check_interval_seconds: int
    max_concurrent_http_checks: int
    max_concurrent_browser_checks: int


class CategoryCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class CategoryResponse(BaseModel):
    id: int
    name: str
    slug: str


class TagResponse(BaseModel):
    id: int
    name: str


class UrlPreviewRequest(BaseModel):
    url: str = Field(min_length=5, max_length=2048)
    variant_label: str = Field(default="", max_length=200)


class UrlPreviewResponse(BaseModel):
    ok: bool
    title: str | None = None
    current_price: Decimal | None = None
    regular_price: Decimal | None = None
    currency: str | None = None
    availability: AvailabilityStatus = AvailabilityStatus.UNKNOWN
    image_url: str | None = None
    store_name: str | None = None
    adapter_key: str
    extraction_layer: ExtractionLayer | None = None
    error_code: ErrorCode = ErrorCode.NONE
    error_detail: str | None = None


class OfferResponse(BaseModel):
    id: int
    product_id: int
    store_id: int
    store_name: str
    store_domain: str
    url: str
    variant_label: str
    is_active: bool
    check_interval_seconds: int | None
    next_check_at: datetime | None
    last_checked_at: datetime | None
    current_price: Decimal | None
    regular_price: Decimal | None
    previous_price: Decimal | None
    currency: str | None
    availability_status: AvailabilityStatus
    last_price_change_at: datetime | None
    consecutive_failures: int
    last_error_code: ErrorCode
    last_error_message: str | None


class ProductResponse(BaseModel):
    id: int
    name: str
    category: CategoryResponse | None
    tags: list[str]
    notes: str | None
    image_url: str | None
    created_at: datetime
    updated_at: datetime
    offers: list[OfferResponse]


class ProductCreateRequest(BaseModel):
    url: str = Field(min_length=5, max_length=2048)
    name: str | None = Field(default=None, max_length=300)
    category_id: int | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    image_url: str | None = Field(default=None, max_length=2048)
    variant_label: str = Field(default="", max_length=200)
    check_interval_seconds: int | None = Field(default=None, ge=300, le=86400 * 7)
    manual_price: Decimal | None = None
    manual_regular_price: Decimal | None = None
    manual_currency: str | None = Field(default=None, max_length=10)
    manual_availability: AvailabilityStatus | None = None
    auto_fetch: bool = True


class ProductUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=300)
    category_id: int | None = None
    clear_category: bool = False
    notes: str | None = None
    image_url: str | None = Field(default=None, max_length=2048)
    tags: list[str] | None = None


class OfferCreateRequest(BaseModel):
    url: str = Field(min_length=5, max_length=2048)
    variant_label: str = Field(default="", max_length=200)
    check_interval_seconds: int | None = Field(default=None, ge=300, le=86400 * 7)
    manual_price: Decimal | None = None
    manual_regular_price: Decimal | None = None
    manual_currency: str | None = Field(default=None, max_length=10)
    manual_availability: AvailabilityStatus | None = None
    auto_fetch: bool = True


class OfferUpdateRequest(BaseModel):
    variant_label: str | None = Field(default=None, max_length=200)
    is_active: bool | None = None
    check_interval_seconds: int | None = Field(default=None, ge=300, le=86400 * 7)
    use_default_interval: bool = False
    manual_price: Decimal | None = None
    manual_regular_price: Decimal | None = None
    manual_currency: str | None = Field(default=None, max_length=10)
    manual_availability: AvailabilityStatus | None = None


class PricePointResponse(BaseModel):
    id: int
    recorded_at: datetime
    price: Decimal
    regular_price: Decimal | None
    currency: str
    availability_status: AvailabilityStatus
    source_layer: ExtractionLayer


class PriceStatsResponse(BaseModel):
    points_count: int
    currency: str | None
    current_price: Decimal | None
    previous_price: Decimal | None
    min_price: Decimal | None
    max_price: Decimal | None
    avg_price: Decimal | None
    last_change_amount: Decimal | None
    last_change_percent: Decimal | None
    period_change_amount: Decimal | None
    period_change_percent: Decimal | None
    lowest_recorded_at: datetime | None
    highest_recorded_at: datetime | None
    first_recorded_at: datetime | None
    last_recorded_at: datetime | None


class OfferHistoryResponse(BaseModel):
    offer_id: int
    product_id: int
    product_name: str
    store_name: str
    store_domain: str
    variant_label: str
    url: str
    time_range: TimeRange
    stats: PriceStatsResponse
    points: list[PricePointResponse]


class ProductComparisonResponse(BaseModel):
    product_id: int
    product_name: str
    time_range: TimeRange
    lowest_current_offer_id: int | None
    offers: list[OfferHistoryResponse]


class RecentPriceChangeResponse(BaseModel):
    offer_id: int
    product_id: int
    product_name: str
    store_name: str
    variant_label: str
    url: str
    previous_price: Decimal
    current_price: Decimal
    currency: str
    change_amount: Decimal
    change_percent: Decimal
    changed_at: datetime


class OfferAttentionResponse(BaseModel):
    offer_id: int
    product_id: int
    product_name: str
    store_name: str
    variant_label: str
    availability_status: AvailabilityStatus
    consecutive_failures: int
    last_error_code: ErrorCode
    last_error_message: str | None
    last_checked_at: datetime | None


class DashboardSummaryResponse(BaseModel):
    total_products: int
    total_offers: int
    active_offers: int
    paused_offers: int
    offers_with_errors: int
    out_of_stock_offers: int
    price_drops_24h: int
    price_rises_24h: int
    recent_changes: list[RecentPriceChangeResponse]
    attention_items: list[OfferAttentionResponse]


class StoreResponse(BaseModel):
    id: int
    name: str
    domain: str
    adapter_key: str
    rate_limit_seconds: int
    requires_js: bool
    custom_selectors_json: dict[str, Any] | None
    is_enabled: bool
    last_error_summary: str | None
    offers_count: int
    active_offers_count: int
    failing_offers_count: int
    success_rate_percent: Decimal | None


class StoreUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=150)
    adapter_key: str | None = Field(default=None, max_length=100)
    rate_limit_seconds: int | None = Field(default=None, ge=0, le=3600)
    requires_js: bool | None = None
    custom_selectors_json: dict[str, str] | None = None
    clear_custom_selectors: bool = False
    is_enabled: bool | None = None


class AdapterInfoResponse(BaseModel):
    adapter_key: str
    display_name: str
    supported_domains: list[str]
    prefer_js: bool


class CheckJobResponse(BaseModel):
    id: int
    offer_id: int
    product_name: str
    store_name: str
    status: CheckJobStatus
    priority: int
    trigger_type: CheckTriggerType
    scheduled_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    attempt_number: int
    http_status: int | None
    duration_ms: int | None
    adapter_used: str | None
    extraction_layer: ExtractionLayer | None
    error_code: ErrorCode
    error_detail: str | None


class EnqueueCheckResponse(BaseModel):
    job_id: int
    offer_id: int


class SchedulerStatusResponse(BaseModel):
    is_running: bool
    is_paused: bool
    active_workers: int
    pending_jobs: int

class AlertRuleCreateRequest(BaseModel):
    offer_id: int | None = None
    product_id: int | None = None
    rule_type: AlertRuleType
    threshold_value: Decimal | None = None
    cooldown_minutes: int = Field(default=60, ge=0, le=10080)
    is_enabled: bool = True


class AlertRuleUpdateRequest(BaseModel):
    threshold_value: Decimal | None = None
    is_enabled: bool | None = None
    cooldown_minutes: int | None = Field(default=None, ge=0, le=10080)
    reset_state: bool = False


class AlertRuleResponse(BaseModel):
    id: int
    offer_id: int | None
    product_id: int | None
    product_name: str | None
    store_name: str | None
    variant_label: str | None
    rule_type: AlertRuleType
    threshold_value: Decimal | None
    is_enabled: bool
    cooldown_minutes: int
    condition_active: bool
    last_triggered_at: datetime | None
    last_triggered_price: Decimal | None


class TelegramChannelConfigRequest(BaseModel):
    chat_id: str = Field(min_length=1, max_length=64)
    bot_token: str | None = Field(default=None, max_length=256)
    name: str = Field(default="Telegram Bot", max_length=120)
    is_enabled: bool = True
    disable_web_page_preview: bool = False


class NotificationChannelResponse(BaseModel):
    id: int
    channel_type: NotificationChannelType
    name: str
    config_json: dict[str, Any]
    is_enabled: bool
    has_secret: bool


class TelegramTestResponse(BaseModel):
    ok: bool
    provider_message_id: str | None = None
    error_message: str | None = None


class NotificationLogResponse(BaseModel):
    id: int
    alert_rule_id: int | None
    offer_id: int | None
    channel_id: int | None
    dedup_key: str
    status: NotificationDeliveryStatus
    payload_summary: str
    error_message: str | None
    created_at: datetime

class AppSettingsResponse(BaseModel):
    default_check_interval_seconds: int
    max_concurrent_http_checks: int
    max_concurrent_browser_checks: int
    job_history_retention_days: int
    theme: Literal["dark", "light"] = "dark"


class AppSettingsUpdateRequest(BaseModel):
    default_check_interval_seconds: int | None = Field(default=None, ge=300, le=86400 * 7)
    theme: Literal["dark", "light"] | None = None


class LogEntryResponse(BaseModel):
    timestamp: str | None = None
    level: str = "info"
    logger: str | None = None
    event: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
