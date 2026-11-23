from __future__ import annotations

import hashlib
import html
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from decimal import Decimal

from price_tracker.core.enums import AlertRuleType, AvailabilityStatus, ErrorCode

TWO_PLACES = Decimal("0.01")
_AVAILABLE_STATUSES = frozenset(
    {
        AvailabilityStatus.IN_STOCK,
        AvailabilityStatus.LIMITED,
        AvailabilityStatus.PREORDER,
    }
)


@dataclass(frozen=True, slots=True)
class AlertEvaluationContext:
    """Pełny kontekst zdarzenia sprawdzania ceny potrzebny do oceny reguł i budowy wiadomości."""

    offer_id: int
    product_id: int
    product_name: str
    store_name: str
    variant_label: str
    url: str
    ok: bool
    previous_price: Decimal | None
    current_price: Decimal | None
    currency: str | None
    previous_availability: AvailabilityStatus
    current_availability: AvailabilityStatus
    consecutive_failures: int
    error_code: ErrorCode
    error_detail: str | None
    checked_at: datetime


@dataclass(frozen=True, slots=True)
class RuleSnapshot:
    """Stan reguły alertu odczytany z bazy przed ewaluacją."""

    rule_id: int
    rule_type: AlertRuleType
    threshold_value: Decimal | None
    cooldown_minutes: int
    condition_active: bool
    last_triggered_at: datetime | None
    last_triggered_price: Decimal | None


@dataclass(frozen=True, slots=True)
class EvaluationDecision:
    """Wynik oceny reguły wraz z nowym stanem do zapisania w bazie."""

    should_notify: bool
    new_condition_active: bool
    new_last_triggered_price: Decimal | None
    dedup_key: str | None = None
    reason_title: str = ""
    suppressed_reason: str | None = None


def _is_in_cooldown(rule: RuleSnapshot, now: datetime) -> bool:
    if rule.last_triggered_at is None or rule.cooldown_minutes <= 0:
        return False
    return (now - rule.last_triggered_at) < timedelta(minutes=rule.cooldown_minutes)


def _make_dedup_key(*parts: object) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _fmt_price(amount: Decimal | None, currency: str | None) -> str:
    if amount is None:
        return "brak danych"
    curr = currency or "PLN"
    return f"{amount.quantize(TWO_PLACES):.2f} {curr}"


def evaluate_alert_rule(rule: RuleSnapshot, ctx: AlertEvaluationContext) -> EvaluationDecision:
    """Deterministycznie ocenia regułę alertu ze stanową histerezą i ochroną przed duplikatami."""
    rt = rule.rule_type
    curr = ctx.currency or "PLN"

    # 1. Błąd monitorowania
    if rt == AlertRuleType.MONITORING_ERROR:
        if ctx.ok:
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=False,
                new_last_triggered_price=rule.last_triggered_price,
            )
        min_failures = max(1, int(rule.threshold_value)) if rule.threshold_value is not None else 1
        if ctx.consecutive_failures < min_failures:
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=rule.condition_active,
                new_last_triggered_price=rule.last_triggered_price,
            )
        if rule.condition_active:
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=True,
                new_last_triggered_price=rule.last_triggered_price,
                suppressed_reason="monitoring_error_already_reported",
            )
        if _is_in_cooldown(rule, ctx.checked_at):
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=rule.condition_active,
                new_last_triggered_price=rule.last_triggered_price,
                suppressed_reason="cooldown_active",
            )
        return EvaluationDecision(
            should_notify=True,
            new_condition_active=True,
            new_last_triggered_price=ctx.current_price,
            dedup_key=_make_dedup_key(
                rule.rule_id, ctx.offer_id, rt.value, ctx.error_code.value, ctx.consecutive_failures
            ),
            reason_title=f"Błąd monitorowania ({ctx.error_code.value})",
        )

    # Pozostałe reguły działają wyłącznie na poprawnych pomiarach
    if not ctx.ok:
        return EvaluationDecision(
            should_notify=False,
            new_condition_active=rule.condition_active,
            new_last_triggered_price=rule.last_triggered_price,
        )

    # 2. Powrót do dostępności
    if rt == AlertRuleType.BACK_IN_STOCK:
        if ctx.current_availability == AvailabilityStatus.OUT_OF_STOCK:
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=False,
                new_last_triggered_price=rule.last_triggered_price,
            )
        returned = (
            ctx.previous_availability == AvailabilityStatus.OUT_OF_STOCK
            and ctx.current_availability in _AVAILABLE_STATUSES
        )
        if not returned:
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=rule.condition_active,
                new_last_triggered_price=rule.last_triggered_price,
            )
        if rule.condition_active:
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=True,
                new_last_triggered_price=rule.last_triggered_price,
                suppressed_reason="back_in_stock_already_reported",
            )
        if _is_in_cooldown(rule, ctx.checked_at):
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=rule.condition_active,
                new_last_triggered_price=rule.last_triggered_price,
                suppressed_reason="cooldown_active",
            )
        return EvaluationDecision(
            should_notify=True,
            new_condition_active=True,
            new_last_triggered_price=ctx.current_price,
            dedup_key=_make_dedup_key(
                rule.rule_id,
                ctx.offer_id,
                rt.value,
                ctx.current_availability.value,
                ctx.checked_at.date().isoformat(),
            ),
            reason_title="Produkt wrócił do dostępności",
        )

    # 3. Wyprzedanie produktu
    if rt == AlertRuleType.OUT_OF_STOCK:
        if ctx.current_availability != AvailabilityStatus.OUT_OF_STOCK:
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=False,
                new_last_triggered_price=rule.last_triggered_price,
            )
        if rule.condition_active:
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=True,
                new_last_triggered_price=rule.last_triggered_price,
                suppressed_reason="out_of_stock_already_reported",
            )
        if _is_in_cooldown(rule, ctx.checked_at):
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=rule.condition_active,
                new_last_triggered_price=rule.last_triggered_price,
                suppressed_reason="cooldown_active",
            )
        return EvaluationDecision(
            should_notify=True,
            new_condition_active=True,
            new_last_triggered_price=ctx.current_price,
            dedup_key=_make_dedup_key(
                rule.rule_id,
                ctx.offer_id,
                rt.value,
                ctx.checked_at.date().isoformat(),
            ),
            reason_title="Produkt wyprzedany (brak w magazynie)",
        )

    # Reguły cenowe wymagają poprawnej bieżącej ceny
    if ctx.current_price is None:
        return EvaluationDecision(
            should_notify=False,
            new_condition_active=rule.condition_active,
            new_last_triggered_price=rule.last_triggered_price,
        )

    # 4. Osiągnięcie ceny docelowej (cena <= próg) z histerezą
    if rt == AlertRuleType.TARGET_PRICE_BELOW:
        if rule.threshold_value is None:
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=False,
                new_last_triggered_price=rule.last_triggered_price,
            )
        if ctx.current_price > rule.threshold_value:
            # Cena wróciła powyżej progu -> reset histerezy
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=False,
                new_last_triggered_price=rule.last_triggered_price,
            )

        if rule.condition_active and (
            rule.last_triggered_price is None or ctx.current_price >= rule.last_triggered_price
        ):
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=True,
                new_last_triggered_price=rule.last_triggered_price,
                suppressed_reason="target_price_already_notified",
            )

        if _is_in_cooldown(rule, ctx.checked_at):
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=rule.condition_active,
                new_last_triggered_price=rule.last_triggered_price,
                suppressed_reason="cooldown_active",
            )

        threshold_str = _fmt_price(rule.threshold_value, curr)
        return EvaluationDecision(
            should_notify=True,
            new_condition_active=True,
            new_last_triggered_price=ctx.current_price,
            dedup_key=_make_dedup_key(
                rule.rule_id,
                ctx.offer_id,
                rt.value,
                str(rule.threshold_value.quantize(TWO_PLACES)),
                str(ctx.current_price.quantize(TWO_PLACES)),
            ),
            reason_title=f"Cena osiągnęła próg ≤ {threshold_str}",
        )

    # Pozostałe reguły cenowe wymagają poprzedniej ceny oraz faktycznej zmiany
    if ctx.previous_price is None or ctx.previous_price == ctx.current_price:
        return EvaluationDecision(
            should_notify=False,
            new_condition_active=False,
            new_last_triggered_price=rule.last_triggered_price,
        )

    # 5. Spadek ceny (opcjonalny minimalny próg kwotowy)
    if rt == AlertRuleType.PRICE_DROP:
        drop_amount = (ctx.previous_price - ctx.current_price).quantize(TWO_PLACES)
        min_drop = rule.threshold_value if rule.threshold_value is not None else Decimal("0.01")
        if drop_amount < min_drop:
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=False,
                new_last_triggered_price=rule.last_triggered_price,
            )
        if _is_in_cooldown(rule, ctx.checked_at):
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=rule.condition_active,
                new_last_triggered_price=rule.last_triggered_price,
                suppressed_reason="cooldown_active",
            )
        return EvaluationDecision(
            should_notify=True,
            new_condition_active=False,
            new_last_triggered_price=ctx.current_price,
            dedup_key=_make_dedup_key(
                rule.rule_id,
                ctx.offer_id,
                rt.value,
                str(ctx.previous_price.quantize(TWO_PLACES)),
                str(ctx.current_price.quantize(TWO_PLACES)),
            ),
            reason_title="Spadek ceny produktu",
        )

    # 6. Wzrost ceny (opcjonalny minimalny próg kwotowy)
    if rt == AlertRuleType.PRICE_RISE:
        rise_amount = (ctx.current_price - ctx.previous_price).quantize(TWO_PLACES)
        min_rise = rule.threshold_value if rule.threshold_value is not None else Decimal("0.01")
        if rise_amount < min_rise:
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=False,
                new_last_triggered_price=rule.last_triggered_price,
            )
        if _is_in_cooldown(rule, ctx.checked_at):
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=rule.condition_active,
                new_last_triggered_price=rule.last_triggered_price,
                suppressed_reason="cooldown_active",
            )
        return EvaluationDecision(
            should_notify=True,
            new_condition_active=False,
            new_last_triggered_price=ctx.current_price,
            dedup_key=_make_dedup_key(
                rule.rule_id,
                ctx.offer_id,
                rt.value,
                str(ctx.previous_price.quantize(TWO_PLACES)),
                str(ctx.current_price.quantize(TWO_PLACES)),
            ),
            reason_title="Wzrost ceny produktu",
        )

    # 7. Spadek ceny o określony procent
    if rt == AlertRuleType.PERCENT_DROP:
        if rule.threshold_value is None or ctx.previous_price <= 0:
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=False,
                new_last_triggered_price=rule.last_triggered_price,
            )
        drop_pct = (
            ((ctx.previous_price - ctx.current_price) / ctx.previous_price) * Decimal("100")
        ).quantize(TWO_PLACES)
        if drop_pct < rule.threshold_value:
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=False,
                new_last_triggered_price=rule.last_triggered_price,
            )
        if _is_in_cooldown(rule, ctx.checked_at):
            return EvaluationDecision(
                should_notify=False,
                new_condition_active=rule.condition_active,
                new_last_triggered_price=rule.last_triggered_price,
                suppressed_reason="cooldown_active",
            )
        return EvaluationDecision(
            should_notify=True,
            new_condition_active=False,
            new_last_triggered_price=ctx.current_price,
            dedup_key=_make_dedup_key(
                rule.rule_id,
                ctx.offer_id,
                rt.value,
                str(ctx.previous_price.quantize(TWO_PLACES)),
                str(ctx.current_price.quantize(TWO_PLACES)),
            ),
            reason_title=f"Spadek ceny o {drop_pct:.2f}% (próg {rule.threshold_value:.2f}%)",
        )

    return EvaluationDecision(
        should_notify=False,
        new_condition_active=rule.condition_active,
        new_last_triggered_price=rule.last_triggered_price,
    )


def format_telegram_message(
    ctx: AlertEvaluationContext,
    decision: EvaluationDecision,
    *,
    display_tz: tzinfo | None = None,
) -> str:
    """Buduje czytelną wiadomość HTML dla Telegram Bot API ze wszystkimi wymaganymi polami."""
    title = html.escape(decision.reason_title or "Alert cenowy")
    product_label = ctx.product_name
    if ctx.variant_label:
        product_label = f"{product_label} ({ctx.variant_label})"

    safe_product = html.escape(product_label)
    safe_store = html.escape(ctx.store_name)
    safe_url = html.escape(ctx.url, quote=True)

    prev_str = _fmt_price(ctx.previous_price, ctx.currency)
    curr_str = _fmt_price(ctx.current_price, ctx.currency)

    diff_str = "brak zmiany"
    if (
        ctx.previous_price is not None
        and ctx.current_price is not None
        and ctx.previous_price > 0
    ):
        diff = (ctx.current_price - ctx.previous_price).quantize(TWO_PLACES)
        pct = ((diff / ctx.previous_price) * Decimal("100")).quantize(TWO_PLACES)
        sign = "+" if diff > 0 else ""
        curr = ctx.currency or "PLN"
        diff_str = f"{sign}{diff:.2f} {curr} ({sign}{pct:.2f}%)"

    local_dt = (
        ctx.checked_at.astimezone(display_tz)
        if display_tz is not None
        else ctx.checked_at.astimezone()
    )
    dt_str = local_dt.strftime("%Y-%m-%d %H:%M:%S %Z").strip()

    lines = [
        f"<b>{title}</b>",
        "",
        f"<b>Produkt:</b> {safe_product}",
        f"<b>Sklep:</b> {safe_store}",
        f"<b>Poprzednia cena:</b> {html.escape(prev_str)}",
        f"<b>Aktualna cena:</b> {html.escape(curr_str)}",
        f"<b>Różnica:</b> {html.escape(diff_str)}",
        f"<b>Wykryto:</b> {html.escape(dt_str)}",
    ]

    if not ctx.ok and ctx.error_detail:
        lines.append(f"<b>Diagnostyka:</b> {html.escape(ctx.error_detail[:300])}")

    lines.append(f'<b>Link:</b> <a href="{safe_url}">Otwórz ofertę</a>')
    return "\n".join(lines)
