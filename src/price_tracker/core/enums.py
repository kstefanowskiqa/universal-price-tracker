from __future__ import annotations

from enum import StrEnum


class AvailabilityStatus(StrEnum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    PREORDER = "preorder"
    LIMITED = "limited"
    UNKNOWN = "unknown"


class CheckJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"
    CANCELLED = "cancelled"


class CheckTriggerType(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"
    INITIAL_ADD = "initial_add"


class ExtractionLayer(StrEnum):
    OFFICIAL_API = "official_api"
    STORE_ADAPTER = "store_adapter"
    JSON_LD = "json_ld"
    MICRODATA = "microdata"
    OPEN_GRAPH = "open_graph"
    CSS_SELECTOR = "css_selector"
    HEURISTIC = "heuristic"
    PLAYWRIGHT = "playwright"
    MANUAL = "manual"


class ErrorCode(StrEnum):
    NONE = "none"
    HTTP_ERROR = "http_error"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    SSRF_BLOCKED = "ssrf_blocked"
    BLOCKED_WAF = "blocked_waf"
    BLOCKED_CAPTCHA = "blocked_captcha"
    AUTH_REQUIRED = "auth_required"
    RATE_LIMITED = "rate_limited"
    PARSE_PRICE_NOT_FOUND = "parse_price_not_found"
    PARSE_VARIANT_REQUIRED = "parse_variant_required"
    CURRENCY_MISMATCH = "currency_mismatch"
    ANOMALY_REJECTED = "anomaly_rejected"
    UNEXPECTED_ERROR = "unexpected_error"


class AlertRuleType(StrEnum):
    PRICE_DROP = "price_drop"
    PRICE_RISE = "price_rise"
    TARGET_PRICE_BELOW = "target_price_below"
    PERCENT_DROP = "percent_drop"
    BACK_IN_STOCK = "back_in_stock"
    OUT_OF_STOCK = "out_of_stock"
    MONITORING_ERROR = "monitoring_error"


class NotificationChannelType(StrEnum):
    TELEGRAM = "telegram"


class NotificationDeliveryStatus(StrEnum):
    SENT = "sent"
    FAILED = "failed"
    SUPPRESSED = "suppressed"
