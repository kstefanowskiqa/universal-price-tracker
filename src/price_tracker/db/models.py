from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
from price_tracker.db.base import Base, TimestampMixin, utc_now
from price_tracker.db.types import MoneyDecimal, UTCDateTime


def _enum_col(enum_cls: type[Any], length: int = 32) -> SAEnum:
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda cls: [item.value for item in cls],
    )


class Category(TimestampMixin, Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(140), nullable=False, unique=True)

    products: Mapped[list[Product]] = relationship(back_populates="category")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)

    product_links: Mapped[list[ProductTag]] = relationship(
        back_populates="tag", cascade="all, delete-orphan"
    )


class ProductTag(Base):
    __tablename__ = "product_tags"

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )

    product: Mapped[Product] = relationship(back_populates="tag_links")
    tag: Mapped[Tag] = relationship(back_populates="product_links")


class Product(TimestampMixin, Base):
    """Produkt logiczny (kanoniczny) łączący monitorowane oferty z wielu sklepów."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    category: Mapped[Category | None] = relationship(back_populates="products")
    tag_links: Mapped[list[ProductTag]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    offers: Mapped[list[TrackedOffer]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    alert_rules: Mapped[list[AlertRule]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class Store(TimestampMixin, Base):
    """Sklep internetowy identyfikowany po domenie oraz konfiguracja jego adaptera."""

    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    adapter_key: Mapped[str] = mapped_column(
        String(100), nullable=False, default="universal", server_default="universal"
    )
    rate_limit_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )
    requires_js: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    custom_selectors_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    last_error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    offers: Mapped[list[TrackedOffer]] = relationship(back_populates="store")


class TrackedOffer(TimestampMixin, Base):
    """Monitorowany adres URL (oraz opcjonalny wariant) przypisany do produktu i sklepu."""

    __tablename__ = "tracked_offers"
    __table_args__ = (
        UniqueConstraint("url", "variant_label", name="uq_tracked_offers_url_variant"),
        Index("ix_tracked_offers_schedule", "is_active", "next_check_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    store_id: Mapped[int] = mapped_column(
        ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    variant_label: Mapped[str] = mapped_column(
        String(200), nullable=False, default="", server_default=""
    )
    variant_selector_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Harmonogram
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    check_interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_check_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    # Cache ostatniego poprawnego pomiaru (do szybkich widoków tabelarycznych)
    current_price: Mapped[Decimal | None] = mapped_column(MoneyDecimal(), nullable=True)
    regular_price: Mapped[Decimal | None] = mapped_column(MoneyDecimal(), nullable=True)
    previous_price: Mapped[Decimal | None] = mapped_column(MoneyDecimal(), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    availability_status: Mapped[AvailabilityStatus] = mapped_column(
        _enum_col(AvailabilityStatus),
        nullable=False,
        default=AvailabilityStatus.UNKNOWN,
        server_default=AvailabilityStatus.UNKNOWN.value,
    )
    last_price_change_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    # Diagnostyka bieżącego stanu monitorowania
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error_code: Mapped[ErrorCode] = mapped_column(
        _enum_col(ErrorCode, length=48),
        nullable=False,
        default=ErrorCode.NONE,
        server_default=ErrorCode.NONE.value,
    )
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    product: Mapped[Product] = relationship(back_populates="offers")
    store: Mapped[Store] = relationship(back_populates="offers")
    price_history: Mapped[list[PriceHistory]] = relationship(
        back_populates="offer", cascade="all, delete-orphan"
    )
    check_jobs: Mapped[list[CheckJob]] = relationship(
        back_populates="offer", cascade="all, delete-orphan"
    )
    alert_rules: Mapped[list[AlertRule]] = relationship(
        back_populates="offer", cascade="all, delete-orphan"
    )


class PriceHistory(Base):
    """Rzeczywiste udane pomiary cen (append-only, wyłącznie zweryfikowane dane)."""

    __tablename__ = "price_history"
    __table_args__ = (Index("ix_price_history_offer_recorded", "offer_id", "recorded_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    offer_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_offers.id", ondelete="CASCADE"), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    price: Mapped[Decimal] = mapped_column(MoneyDecimal(), nullable=False)
    regular_price: Mapped[Decimal | None] = mapped_column(MoneyDecimal(), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    availability_status: Mapped[AvailabilityStatus] = mapped_column(
        _enum_col(AvailabilityStatus),
        nullable=False,
        default=AvailabilityStatus.UNKNOWN,
    )
    source_layer: Mapped[ExtractionLayer] = mapped_column(
        _enum_col(ExtractionLayer),
        nullable=False,
        default=ExtractionLayer.JSON_LD,
    )

    offer: Mapped[TrackedOffer] = relationship(back_populates="price_history")


class CheckJob(Base):
    """Kolejka oraz historia diagnostyczna prób pobrania danych dla ofert."""

    __tablename__ = "check_jobs"
    __table_args__ = (
        Index("ix_check_jobs_status_priority_scheduled", "status", "priority", "scheduled_at"),
        Index("ix_check_jobs_offer_scheduled", "offer_id", "scheduled_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    offer_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_offers.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[CheckJobStatus] = mapped_column(
        _enum_col(CheckJobStatus),
        nullable=False,
        default=CheckJobStatus.PENDING,
        server_default=CheckJobStatus.PENDING.value,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=10, server_default="10")
    trigger_type: Mapped[CheckTriggerType] = mapped_column(
        _enum_col(CheckTriggerType),
        nullable=False,
        default=CheckTriggerType.SCHEDULED,
        server_default=CheckTriggerType.SCHEDULED.value,
    )
    scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    attempt_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adapter_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    extraction_layer: Mapped[ExtractionLayer | None] = mapped_column(
        _enum_col(ExtractionLayer), nullable=True
    )
    error_code: Mapped[ErrorCode] = mapped_column(
        _enum_col(ErrorCode, length=48),
        nullable=False,
        default=ErrorCode.NONE,
        server_default=ErrorCode.NONE.value,
    )
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    offer: Mapped[TrackedOffer] = relationship(back_populates="check_jobs")


class AlertRule(TimestampMixin, Base):
    """Reguły powiadomień powiązane z konkretną ofertą lub całym produktem."""

    __tablename__ = "alert_rules"
    __table_args__ = (
        CheckConstraint(
            "offer_id IS NOT NULL OR product_id IS NOT NULL",
            name="alert_target_present",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    offer_id: Mapped[int | None] = mapped_column(
        ForeignKey("tracked_offers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=True, index=True
    )
    rule_type: Mapped[AlertRuleType] = mapped_column(_enum_col(AlertRuleType), nullable=False)
    threshold_value: Mapped[Decimal | None] = mapped_column(MoneyDecimal(), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    cooldown_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60, server_default="60"
    )

    # Stan deduplikacji / histerezy
    condition_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    last_triggered_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_triggered_price: Mapped[Decimal | None] = mapped_column(MoneyDecimal(), nullable=True)

    offer: Mapped[TrackedOffer | None] = relationship(back_populates="alert_rules")
    product: Mapped[Product | None] = relationship(back_populates="alert_rules")
    notification_logs: Mapped[list[NotificationLog]] = relationship(back_populates="alert_rule")


class NotificationChannel(TimestampMixin, Base):
    """Konfiguracja kanałów powiadomień (sekrety przechowywane wyłącznie w SecretStore)."""

    __tablename__ = "notification_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_type: Mapped[NotificationChannelType] = mapped_column(
        _enum_col(NotificationChannelType), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    secret_key_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )

    logs: Mapped[list[NotificationLog]] = relationship(back_populates="channel")


class NotificationLog(Base):
    """Historia wysłanych, zablokowanych (deduplikacja) i nieudanych powiadomień."""

    __tablename__ = "notification_logs"
    __table_args__ = (Index("ix_notification_logs_dedup_created", "dedup_key", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="SET NULL"), nullable=True
    )
    offer_id: Mapped[int | None] = mapped_column(
        ForeignKey("tracked_offers.id", ondelete="SET NULL"), nullable=True
    )
    channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="SET NULL"), nullable=True
    )
    dedup_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[NotificationDeliveryStatus] = mapped_column(
        _enum_col(NotificationDeliveryStatus), nullable=False
    )
    payload_summary: Mapped[str] = mapped_column(Text, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    alert_rule: Mapped[AlertRule | None] = relationship(back_populates="notification_logs")
    channel: Mapped[NotificationChannel | None] = relationship(back_populates="logs")


class AppSetting(TimestampMixin, Base):
    """Ustawienia globalne aplikacji przechowywane w bazie danych (klucz -> wartość JSON)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value_json: Mapped[Any] = mapped_column(JSON, nullable=False)
