from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from price_tracker.config.secrets import SecretStore, get_secret_store
from price_tracker.core.enums import (
    AlertRuleType,
    NotificationChannelType,
    NotificationDeliveryStatus,
)
from price_tracker.core.exceptions import NotFoundError, ValidationError
from price_tracker.db.models import (
    AlertRule,
    NotificationChannel,
    NotificationLog,
    Product,
    TrackedOffer,
)
from price_tracker.db.session import DatabaseManager
from price_tracker.logging.setup import get_logger
from price_tracker.notifications.channels import (
    ChannelDeliveryResult,
    ChannelRegistry,
    build_default_channel_registry,
)
from price_tracker.notifications.evaluator import (
    AlertEvaluationContext,
    RuleSnapshot,
    evaluate_alert_rule,
    format_telegram_message,
)
from price_tracker.scheduler.worker import PriceCheckEvent

logger = get_logger(__name__)

TELEGRAM_SECRET_KEY = "telegram_bot_token"

_RULES_REQUIRING_THRESHOLD = frozenset(
    {
        AlertRuleType.TARGET_PRICE_BELOW,
        AlertRuleType.PERCENT_DROP,
    }
)


@dataclass(frozen=True, slots=True)
class AlertRuleView:
    rule: AlertRule
    product_name: str | None
    store_name: str | None
    variant_label: str | None


@dataclass(frozen=True, slots=True)
class ChannelView:
    channel: NotificationChannel
    has_secret: bool


class NotificationService:
    """Zarządza regułami alertów, kanałami powiadomień, deduplikacją i wysyłką wiadomości."""

    def __init__(
        self,
        db_manager: DatabaseManager,
        *,
        secret_store: SecretStore | None = None,
        channel_registry: ChannelRegistry | None = None,
        dedup_window_hours: int = 24,
    ) -> None:
        self._db = db_manager
        self._secrets = secret_store or get_secret_store()
        self._channels = channel_registry or build_default_channel_registry()
        self._dedup_window = timedelta(hours=dedup_window_hours)

    # ---- Zarządzanie regułami alertów ----

    async def create_alert_rule(
        self,
        *,
        rule_type: AlertRuleType,
        offer_id: int | None = None,
        product_id: int | None = None,
        threshold_value: Decimal | None = None,
        cooldown_minutes: int = 60,
        is_enabled: bool = True,
    ) -> AlertRuleView:
        if offer_id is None and product_id is None:
            raise ValidationError("Reguła alertu musi wskazywać ofertę (offer_id) lub produkt.")

        if rule_type in _RULES_REQUIRING_THRESHOLD and threshold_value is None:
            raise ValidationError(
                f"Typ reguły '{rule_type.value}' wymaga podania wartości progowej threshold_value."
            )

        if threshold_value is not None and threshold_value <= 0:
            raise ValidationError("Wartość progowa threshold_value musi być większa od zera.")

        if (
            rule_type == AlertRuleType.PERCENT_DROP
            and threshold_value is not None
            and threshold_value >= Decimal("100")
        ):
            raise ValidationError("Próg procentowy spadku musi być mniejszy niż 100%.")

        if cooldown_minutes < 0 or cooldown_minutes > 10080:
            raise ValidationError("Cooldown musi mieścić się w zakresie 0..10080 minut.")

        async with self._db.session() as session:
            if offer_id is not None:
                offer = await session.get(TrackedOffer, offer_id)
                if offer is None:
                    raise NotFoundError(f"Oferta o id={offer_id} nie istnieje.")
                if product_id is None:
                    product_id = offer.product_id
            elif product_id is not None:
                product = await session.get(Product, product_id)
                if product is None:
                    raise NotFoundError(f"Produkt o id={product_id} nie istnieje.")

            rule = AlertRule(
                offer_id=offer_id,
                product_id=product_id,
                rule_type=rule_type,
                threshold_value=threshold_value,
                cooldown_minutes=cooldown_minutes,
                is_enabled=is_enabled,
            )
            session.add(rule)
            await session.flush()
            rule_id = rule.id

        return await self.get_alert_rule(rule_id)

    async def get_alert_rule(self, rule_id: int) -> AlertRuleView:
        async with self._db.session() as session:
            stmt = (
                select(AlertRule)
                .options(
                    selectinload(AlertRule.offer).selectinload(TrackedOffer.store),
                    selectinload(AlertRule.offer).selectinload(TrackedOffer.product),
                    selectinload(AlertRule.product),
                )
                .where(AlertRule.id == rule_id)
            )
            rule = (await session.execute(stmt)).scalar_one_or_none()
            if rule is None:
                raise NotFoundError(f"Reguła alertu o id={rule_id} nie istnieje.")
            return self._to_rule_view(rule)

    async def list_alert_rules(
        self,
        *,
        offer_id: int | None = None,
        product_id: int | None = None,
    ) -> list[AlertRuleView]:
        async with self._db.session() as session:
            stmt = (
                select(AlertRule)
                .options(
                    selectinload(AlertRule.offer).selectinload(TrackedOffer.store),
                    selectinload(AlertRule.offer).selectinload(TrackedOffer.product),
                    selectinload(AlertRule.product),
                )
                .order_by(AlertRule.created_at.desc(), AlertRule.id.desc())
            )
            if offer_id is not None:
                stmt = stmt.where(AlertRule.offer_id == offer_id)
            if product_id is not None:
                stmt = stmt.where(AlertRule.product_id == product_id)
            rows = list((await session.execute(stmt)).scalars().all())
            return [self._to_rule_view(r) for r in rows]

    async def update_alert_rule(
        self,
        rule_id: int,
        *,
        threshold_value: Decimal | None = None,
        is_enabled: bool | None = None,
        cooldown_minutes: int | None = None,
        reset_state: bool = False,
    ) -> AlertRuleView:
        if threshold_value is not None and threshold_value <= 0:
            raise ValidationError("Wartość progowa threshold_value musi być większa od zera.")
        if cooldown_minutes is not None and (cooldown_minutes < 0 or cooldown_minutes > 10080):
            raise ValidationError("Cooldown musi mieścić się w zakresie 0..10080 minut.")

        async with self._db.session() as session:
            rule = await session.get(AlertRule, rule_id)
            if rule is None:
                raise NotFoundError(f"Reguła alertu o id={rule_id} nie istnieje.")

            if threshold_value is not None:
                rule.threshold_value = threshold_value
                # Zmiana progu resetuje histerezę, aby nowy warunek mógł zadziałać
                rule.condition_active = False
                rule.last_triggered_price = None
            if is_enabled is not None:
                rule.is_enabled = is_enabled
            if cooldown_minutes is not None:
                rule.cooldown_minutes = cooldown_minutes
            if reset_state:
                rule.condition_active = False
                rule.last_triggered_price = None
                rule.last_triggered_at = None

        return await self.get_alert_rule(rule_id)

    async def delete_alert_rule(self, rule_id: int) -> None:
        async with self._db.session() as session:
            rule = await session.get(AlertRule, rule_id)
            if rule is None:
                raise NotFoundError(f"Reguła alertu o id={rule_id} nie istnieje.")
            await session.delete(rule)

    # ---- Zarządzanie kanałami (Telegram) ----

    async def list_channels(self) -> list[ChannelView]:
        async with self._db.session() as session:
            stmt = select(NotificationChannel).order_by(NotificationChannel.id.asc())
            channels = list((await session.execute(stmt)).scalars().all())

        result: list[ChannelView] = []
        for ch in channels:
            has_secret = bool(ch.secret_key_ref and self._secrets.get_secret(ch.secret_key_ref))
            result.append(ChannelView(channel=ch, has_secret=has_secret))
        return result

    async def configure_telegram_channel(
        self,
        *,
        chat_id: str,
        bot_token: str | None = None,
        name: str = "Telegram Bot",
        is_enabled: bool = True,
        disable_web_page_preview: bool = False,
    ) -> ChannelView:
        clean_chat_id = chat_id.strip()
        if not clean_chat_id:
            raise ValidationError("Parametr chat_id nie może być pusty.")

        if bot_token is not None and bot_token.strip():
            self._secrets.set_secret(TELEGRAM_SECRET_KEY, bot_token.strip())

        async with self._db.session() as session:
            stmt = select(NotificationChannel).where(
                NotificationChannel.channel_type == NotificationChannelType.TELEGRAM
            )
            channel = (await session.execute(stmt)).scalar_one_or_none()
            cfg = {
                "chat_id": clean_chat_id,
                "disable_web_page_preview": disable_web_page_preview,
            }
            if channel is None:
                channel = NotificationChannel(
                    channel_type=NotificationChannelType.TELEGRAM,
                    name=name.strip() or "Telegram Bot",
                    config_json=cfg,
                    secret_key_ref=TELEGRAM_SECRET_KEY,
                    is_enabled=is_enabled,
                )
                session.add(channel)
            else:
                channel.name = name.strip() or channel.name
                channel.config_json = cfg
                channel.secret_key_ref = TELEGRAM_SECRET_KEY
                channel.is_enabled = is_enabled
            await session.flush()

        has_secret = bool(self._secrets.get_secret(TELEGRAM_SECRET_KEY))
        return ChannelView(channel=channel, has_secret=has_secret)

    async def send_telegram_test_message(self) -> ChannelDeliveryResult:
        async with self._db.session() as session:
            stmt = select(NotificationChannel).where(
                NotificationChannel.channel_type == NotificationChannelType.TELEGRAM
            )
            channel = (await session.execute(stmt)).scalar_one_or_none()
            if channel is None:
                raise NotFoundError("Kanał Telegram nie został jeszcze skonfigurowany.")
            config = dict(channel.config_json)
            secret_ref = channel.secret_key_ref or TELEGRAM_SECRET_KEY
            channel_id = channel.id

        client = self._channels.get(NotificationChannelType.TELEGRAM)
        if client is None:
            return ChannelDeliveryResult(
                ok=False,
                error_message="Brak klienta Telegram w rejestrze.",
            )

        token = self._secrets.get_secret(secret_ref)
        now = datetime.now(UTC)
        dt_str = now.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z").strip()
        text = (
            "<b>Universal Price Tracker – test powiadomień</b>\n\n"
            "Połączenie z Telegram Bot API działa poprawnie.\n"
            f"<b>Czas testu:</b> {dt_str}"
        )
        delivery = await client.send(message=text, config=config, secret=token)

        async with self._db.session() as session:
            session.add(
                NotificationLog(
                    alert_rule_id=None,
                    offer_id=None,
                    channel_id=channel_id,
                    dedup_key=f"test-{int(now.timestamp())}",
                    status=(
                        NotificationDeliveryStatus.SENT
                        if delivery.ok
                        else NotificationDeliveryStatus.FAILED
                    ),
                    payload_summary="Wiadomość testowa Telegram",
                    error_message=delivery.error_message,
                    created_at=now,
                )
            )
        return delivery

    async def list_notification_logs(
        self,
        *,
        offer_id: int | None = None,
        status: NotificationDeliveryStatus | None = None,
        limit: int = 100,
    ) -> list[NotificationLog]:
        async with self._db.session() as session:
            stmt = (
                select(NotificationLog)
                .order_by(NotificationLog.created_at.desc(), NotificationLog.id.desc())
                .limit(min(max(limit, 1), 300))
            )
            if offer_id is not None:
                stmt = stmt.where(NotificationLog.offer_id == offer_id)
            if status is not None:
                stmt = stmt.where(NotificationLog.status == status)
            return list((await session.execute(stmt)).scalars().all())


    # ---- Główna obsługa zdarzeń z harmonogramu ----

    async def handle_price_check_event(self, event: PriceCheckEvent) -> int:
        """Ocenia aktywne reguły po sprawdzeniu ceny i zwraca liczbę wysłanych powiadomień."""
        async with self._db.session() as session:
            offer_stmt = (
                select(TrackedOffer)
                .options(
                    selectinload(TrackedOffer.product),
                    selectinload(TrackedOffer.store),
                )
                .where(TrackedOffer.id == event.offer_id)
            )
            offer = (await session.execute(offer_stmt)).scalar_one_or_none()
            if offer is None:
                return 0

            rules_stmt = select(AlertRule).where(
                AlertRule.is_enabled.is_(True),
                or_(
                    AlertRule.offer_id == offer.id,
                    (AlertRule.offer_id.is_(None)) & (AlertRule.product_id == offer.product_id),
                ),
            )
            rules = list((await session.execute(rules_stmt)).scalars().all())
            if not rules:
                return 0

            channels_stmt = select(NotificationChannel).where(
                NotificationChannel.is_enabled.is_(True)
            )
            active_channels = list((await session.execute(channels_stmt)).scalars().all())

            ctx = AlertEvaluationContext(
                offer_id=offer.id,
                product_id=offer.product_id,
                product_name=offer.product.name,
                store_name=offer.store.name,
                variant_label=offer.variant_label,
                url=offer.url,
                ok=event.ok,
                previous_price=event.previous_price,
                current_price=event.current_price,
                currency=event.currency or offer.currency,
                previous_availability=event.previous_availability,
                current_availability=event.current_availability,
                consecutive_failures=offer.consecutive_failures,
                error_code=event.error_code,
                error_detail=event.error_detail,
                checked_at=event.checked_at,
            )

            to_dispatch: list[tuple[int, str, str, str]] = []
            dedup_cutoff = event.checked_at - self._dedup_window

            for rule in rules:
                snapshot = RuleSnapshot(
                    rule_id=rule.id,
                    rule_type=rule.rule_type,
                    threshold_value=rule.threshold_value,
                    cooldown_minutes=rule.cooldown_minutes,
                    condition_active=rule.condition_active,
                    last_triggered_at=rule.last_triggered_at,
                    last_triggered_price=rule.last_triggered_price,
                )
                decision = evaluate_alert_rule(snapshot, ctx)

                if not decision.should_notify:
                    rule.condition_active = decision.new_condition_active
                    continue

                assert decision.dedup_key is not None
                # Dodatkowa twarda deduplikacja po bazie notification_logs
                dup_stmt = select(NotificationLog.id).where(
                    NotificationLog.dedup_key == decision.dedup_key,
                    NotificationLog.status == NotificationDeliveryStatus.SENT,
                    NotificationLog.created_at >= dedup_cutoff,
                )
                already_sent = (await session.execute(dup_stmt)).scalar_one_or_none()
                if already_sent is not None:
                    session.add(
                        NotificationLog(
                            alert_rule_id=rule.id,
                            offer_id=offer.id,
                            channel_id=None,
                            dedup_key=decision.dedup_key,
                            status=NotificationDeliveryStatus.SUPPRESSED,
                            payload_summary=f"Pominięto duplikat: {decision.reason_title}",
                            created_at=event.checked_at,
                        )
                    )
                    rule.condition_active = decision.new_condition_active
                    continue

                message_html = format_telegram_message(ctx, decision)
                summary = f"{decision.reason_title} | {ctx.product_name} ({ctx.store_name})"
                to_dispatch.append((rule.id, decision.dedup_key, message_html, summary))

                # Aktualizacja stanu reguły po zakwalifikowaniu do wysyłki
                rule.condition_active = decision.new_condition_active
                rule.last_triggered_price = decision.new_last_triggered_price
                rule.last_triggered_at = event.checked_at

            channel_specs = [
                (ch.id, ch.channel_type, dict(ch.config_json), ch.secret_key_ref)
                for ch in active_channels
            ]

        if not to_dispatch or not channel_specs:
            return 0

        # Wysyłka HTTP poza transakcją bazodanową
        sent_count = 0
        delivery_records: list[NotificationLog] = []

        for rule_id, dedup_key, message_html, summary in to_dispatch:
            for ch_id, ch_type, ch_config, secret_ref in channel_specs:
                client = self._channels.get(ch_type)
                if client is None:
                    continue
                secret_val = self._secrets.get_secret(secret_ref) if secret_ref else None
                res = await client.send(
                    message=message_html,
                    config=ch_config,
                    secret=secret_val,
                )
                if res.ok:
                    sent_count += 1
                    logger.info(
                        "notification_sent",
                        channel_type=ch_type.value,
                        rule_id=rule_id,
                        offer_id=event.offer_id,
                    )
                else:
                    logger.warning(
                        "notification_delivery_failed",
                        channel_type=ch_type.value,
                        rule_id=rule_id,
                        offer_id=event.offer_id,
                        error=res.error_message,
                    )

                delivery_records.append(
                    NotificationLog(
                        alert_rule_id=rule_id,
                        offer_id=event.offer_id,
                        channel_id=ch_id,
                        dedup_key=dedup_key,
                        status=(
                            NotificationDeliveryStatus.SENT
                            if res.ok
                            else NotificationDeliveryStatus.FAILED
                        ),
                        payload_summary=summary,
                        error_message=res.error_message,
                        created_at=datetime.now(UTC),
                    )
                )

        if delivery_records:
            async with self._db.session() as session:
                session.add_all(delivery_records)

        return sent_count

    async def close(self) -> None:
        await self._channels.close_all()

    @staticmethod
    def _to_rule_view(rule: AlertRule) -> AlertRuleView:
        product_name: str | None = None
        store_name: str | None = None
        variant_label: str | None = None
        if rule.offer is not None:
            product_name = rule.offer.product.name
            store_name = rule.offer.store.name
            variant_label = rule.offer.variant_label
        elif rule.product is not None:
            product_name = rule.product.name
        return AlertRuleView(
            rule=rule,
            product_name=product_name,
            store_name=store_name,
            variant_label=variant_label,
        )
