"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-22 19:45:00+00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from price_tracker.db.types import MoneyDecimal, UTCDateTime

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=140), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
        sa.UniqueConstraint("name", name=op.f("uq_categories_name")),
        sa.UniqueConstraint("slug", name=op.f("uq_categories_slug")),
    )

    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tags")),
        sa.UniqueConstraint("name", name=op.f("uq_tags_name")),
    )

    op.create_table(
        "stores",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column(
            "adapter_key", sa.String(length=100), server_default="universal", nullable=False
        ),
        sa.Column("rate_limit_seconds", sa.Integer(), server_default="5", nullable=False),
        sa.Column("requires_js", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("custom_selectors_json", sa.JSON(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("last_error_summary", sa.Text(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stores")),
        sa.UniqueConstraint("domain", name=op.f("uq_stores_domain")),
    )
    op.create_index(op.f("ix_stores_domain"), "stores", ["domain"], unique=True)

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("image_url", sa.String(length=2048), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name=op.f("fk_products_category_id_categories"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
    )

    op.create_table(
        "product_tags",
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_product_tags_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["tags.id"],
            name=op.f("fk_product_tags_tag_id_tags"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("product_id", "tag_id", name=op.f("pk_product_tags")),
    )

    op.create_table(
        "tracked_offers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("variant_label", sa.String(length=200), server_default="", nullable=False),
        sa.Column("variant_selector_json", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("check_interval_seconds", sa.Integer(), nullable=True),
        sa.Column("next_check_at", UTCDateTime(), nullable=True),
        sa.Column("last_checked_at", UTCDateTime(), nullable=True),
        sa.Column("current_price", MoneyDecimal(), nullable=True),
        sa.Column("regular_price", MoneyDecimal(), nullable=True),
        sa.Column("previous_price", MoneyDecimal(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column(
            "availability_status",
            sa.String(length=32),
            server_default="unknown",
            nullable=False,
        ),
        sa.Column("last_price_change_at", UTCDateTime(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error_code", sa.String(length=48), server_default="none", nullable=False),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_tracked_offers_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_tracked_offers_store_id_stores"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tracked_offers")),
        sa.UniqueConstraint("url", "variant_label", name="uq_tracked_offers_url_variant"),
    )
    op.create_index(op.f("ix_tracked_offers_product_id"), "tracked_offers", ["product_id"])
    op.create_index(op.f("ix_tracked_offers_store_id"), "tracked_offers", ["store_id"])
    op.create_index(
        "ix_tracked_offers_schedule", "tracked_offers", ["is_active", "next_check_at"]
    )

    op.create_table(
        "price_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("offer_id", sa.Integer(), nullable=False),
        sa.Column("recorded_at", UTCDateTime(), nullable=False),
        sa.Column("price", MoneyDecimal(), nullable=False),
        sa.Column("regular_price", MoneyDecimal(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("availability_status", sa.String(length=32), nullable=False),
        sa.Column("source_layer", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["tracked_offers.id"],
            name=op.f("fk_price_history_offer_id_tracked_offers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_price_history")),
    )
    op.create_index(
        "ix_price_history_offer_recorded", "price_history", ["offer_id", "recorded_at"]
    )

    op.create_table(
        "check_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("offer_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("priority", sa.Integer(), server_default="10", nullable=False),
        sa.Column(
            "trigger_type", sa.String(length=32), server_default="scheduled", nullable=False
        ),
        sa.Column("scheduled_at", UTCDateTime(), nullable=False),
        sa.Column("started_at", UTCDateTime(), nullable=True),
        sa.Column("finished_at", UTCDateTime(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), server_default="1", nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("adapter_used", sa.String(length=100), nullable=True),
        sa.Column("extraction_layer", sa.String(length=32), nullable=True),
        sa.Column("error_code", sa.String(length=48), server_default="none", nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["tracked_offers.id"],
            name=op.f("fk_check_jobs_offer_id_tracked_offers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_check_jobs")),
    )
    op.create_index(op.f("ix_check_jobs_correlation_id"), "check_jobs", ["correlation_id"])
    op.create_index(
        "ix_check_jobs_status_priority_scheduled",
        "check_jobs",
        ["status", "priority", "scheduled_at"],
    )
    op.create_index("ix_check_jobs_offer_scheduled", "check_jobs", ["offer_id", "scheduled_at"])

    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("offer_id", sa.Integer(), nullable=True),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("rule_type", sa.String(length=32), nullable=False),
        sa.Column("threshold_value", MoneyDecimal(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("cooldown_minutes", sa.Integer(), server_default="60", nullable=False),
        sa.Column("condition_active", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("last_triggered_at", UTCDateTime(), nullable=True),
        sa.Column("last_triggered_price", MoneyDecimal(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "offer_id IS NOT NULL OR product_id IS NOT NULL",
            name=op.f("ck_alert_rules_alert_target_present"),
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["tracked_offers.id"],
            name=op.f("fk_alert_rules_offer_id_tracked_offers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_alert_rules_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_rules")),
    )
    op.create_index(op.f("ix_alert_rules_offer_id"), "alert_rules", ["offer_id"])
    op.create_index(op.f("ix_alert_rules_product_id"), "alert_rules", ["product_id"])

    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("channel_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("secret_key_ref", sa.String(length=120), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_channels")),
    )

    op.create_table(
        "notification_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("alert_rule_id", sa.Integer(), nullable=True),
        sa.Column("offer_id", sa.Integer(), nullable=True),
        sa.Column("channel_id", sa.Integer(), nullable=True),
        sa.Column("dedup_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_summary", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["alert_rule_id"],
            ["alert_rules.id"],
            name=op.f("fk_notification_logs_alert_rule_id_alert_rules"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["notification_channels.id"],
            name=op.f("fk_notification_logs_channel_id_notification_channels"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["tracked_offers.id"],
            name=op.f("fk_notification_logs_offer_id_tracked_offers"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_logs")),
    )
    op.create_index(
        "ix_notification_logs_dedup_created", "notification_logs", ["dedup_key", "created_at"]
    )

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_app_settings")),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_index("ix_notification_logs_dedup_created", table_name="notification_logs")
    op.drop_table("notification_logs")
    op.drop_table("notification_channels")
    op.drop_index(op.f("ix_alert_rules_product_id"), table_name="alert_rules")
    op.drop_index(op.f("ix_alert_rules_offer_id"), table_name="alert_rules")
    op.drop_table("alert_rules")
    op.drop_index("ix_check_jobs_offer_scheduled", table_name="check_jobs")
    op.drop_index("ix_check_jobs_status_priority_scheduled", table_name="check_jobs")
    op.drop_index(op.f("ix_check_jobs_correlation_id"), table_name="check_jobs")
    op.drop_table("check_jobs")
    op.drop_index("ix_price_history_offer_recorded", table_name="price_history")
    op.drop_table("price_history")
    op.drop_index("ix_tracked_offers_schedule", table_name="tracked_offers")
    op.drop_index(op.f("ix_tracked_offers_store_id"), table_name="tracked_offers")
    op.drop_index(op.f("ix_tracked_offers_product_id"), table_name="tracked_offers")
    op.drop_table("tracked_offers")
    op.drop_table("product_tags")
    op.drop_table("products")
    op.drop_index(op.f("ix_stores_domain"), table_name="stores")
    op.drop_table("stores")
    op.drop_table("tags")
    op.drop_table("categories")
