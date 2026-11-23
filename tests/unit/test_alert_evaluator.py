from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from price_tracker.core.enums import AlertRuleType, AvailabilityStatus, ErrorCode
from price_tracker.notifications.evaluator import (
    AlertEvaluationContext,
    RuleSnapshot,
    evaluate_alert_rule,
    format_telegram_message,
)


def _ctx(
    *,
    previous_price: str | None = "2500.00",
    current_price: str | None = "2150.00",
    ok: bool = True,
    prev_avail: AvailabilityStatus = AvailabilityStatus.IN_STOCK,
    curr_avail: AvailabilityStatus = AvailabilityStatus.IN_STOCK,
    failures: int = 0,
    error_code: ErrorCode = ErrorCode.NONE,
    checked_at: datetime | None = None,
) -> AlertEvaluationContext:
    return AlertEvaluationContext(
        offer_id=10,
        product_id=1,
        product_name="RTX 4070 SUPER",
        store_name="x-kom",
        variant_label="12GB",
        url="https://www.x-kom.pl/p/12345.html",
        ok=ok,
        previous_price=Decimal(previous_price) if previous_price else None,
        current_price=Decimal(current_price) if current_price else None,
        currency="PLN",
        previous_availability=prev_avail,
        current_availability=curr_avail,
        consecutive_failures=failures,
        error_code=error_code,
        error_detail="Cloudflare WAF" if not ok else None,
        checked_at=checked_at or datetime(2026, 11, 22, 19, 0, tzinfo=UTC),
    )


def test_target_price_below_hysteresis_prevents_duplicates() -> None:
    t0 = datetime(2026, 11, 22, 18, 0, tzinfo=UTC)
    rule = RuleSnapshot(
        rule_id=1,
        rule_type=AlertRuleType.TARGET_PRICE_BELOW,
        threshold_value=Decimal("2200.00"),
        cooldown_minutes=30,
        condition_active=False,
        last_triggered_at=None,
        last_triggered_price=None,
    )

    # 1. Spadek z 2500 -> 2150 (poniżej 2200) => WYSYŁA
    d1 = evaluate_alert_rule(
        rule,
        _ctx(previous_price="2500.00", current_price="2150.00", checked_at=t0),
    )
    assert d1.should_notify is True
    assert d1.new_condition_active is True
    assert d1.new_last_triggered_price == Decimal("2150.00")

    # 2. Kolejny odczyt nadal 2150 po godzinie => NIE WYSYŁA (histereza)
    rule_after_1 = RuleSnapshot(
        rule_id=1,
        rule_type=AlertRuleType.TARGET_PRICE_BELOW,
        threshold_value=Decimal("2200.00"),
        cooldown_minutes=30,
        condition_active=d1.new_condition_active,
        last_triggered_at=t0,
        last_triggered_price=d1.new_last_triggered_price,
    )
    d2 = evaluate_alert_rule(
        rule_after_1,
        _ctx(
            previous_price="2150.00",
            current_price="2150.00",
            checked_at=t0 + timedelta(hours=1),
        ),
    )
    assert d2.should_notify is False
    assert d2.suppressed_reason == "target_price_already_notified"

    # 3. Dalszy spadek do 2099 po minięciu cooldownu => WYSYŁA ponownie
    d3 = evaluate_alert_rule(
        rule_after_1,
        _ctx(
            previous_price="2150.00",
            current_price="2099.00",
            checked_at=t0 + timedelta(hours=2),
        ),
    )
    assert d3.should_notify is True
    assert d3.new_last_triggered_price == Decimal("2099.00")

    # 4. Powrót ceny powyżej 2200 -> reset histerezy
    rule_after_3 = RuleSnapshot(
        rule_id=1,
        rule_type=AlertRuleType.TARGET_PRICE_BELOW,
        threshold_value=Decimal("2200.00"),
        cooldown_minutes=30,
        condition_active=True,
        last_triggered_at=t0 + timedelta(hours=2),
        last_triggered_price=Decimal("2099.00"),
    )
    d4 = evaluate_alert_rule(
        rule_after_3,
        _ctx(
            previous_price="2099.00",
            current_price="2350.00",
            checked_at=t0 + timedelta(hours=3),
        ),
    )
    assert d4.should_notify is False
    assert d4.new_condition_active is False


def test_percent_drop_and_price_rise_rules() -> None:
    pct_rule = RuleSnapshot(
        rule_id=2,
        rule_type=AlertRuleType.PERCENT_DROP,
        threshold_value=Decimal("10.00"),
        cooldown_minutes=0,
        condition_active=False,
        last_triggered_at=None,
        last_triggered_price=None,
    )
    # 2500 -> 2150 to -14% >= 10%
    dec = evaluate_alert_rule(pct_rule, _ctx(previous_price="2500.00", current_price="2150.00"))
    assert dec.should_notify is True

    # 2500 -> 2400 to -4% < 10%
    dec_small = evaluate_alert_rule(
        pct_rule, _ctx(previous_price="2500.00", current_price="2400.00")
    )
    assert dec_small.should_notify is False

    rise_rule = RuleSnapshot(
        rule_id=3,
        rule_type=AlertRuleType.PRICE_RISE,
        threshold_value=Decimal("50.00"),
        cooldown_minutes=0,
        condition_active=False,
        last_triggered_at=None,
        last_triggered_price=None,
    )
    dec_rise = evaluate_alert_rule(
        rise_rule, _ctx(previous_price="2500.00", current_price="2600.00")
    )
    assert dec_rise.should_notify is True


def test_stock_and_monitoring_error_rules() -> None:
    back_rule = RuleSnapshot(
        rule_id=4,
        rule_type=AlertRuleType.BACK_IN_STOCK,
        threshold_value=None,
        cooldown_minutes=0,
        condition_active=False,
        last_triggered_at=None,
        last_triggered_price=None,
    )
    dec_back = evaluate_alert_rule(
        back_rule,
        _ctx(prev_avail=AvailabilityStatus.OUT_OF_STOCK, curr_avail=AvailabilityStatus.IN_STOCK),
    )
    assert dec_back.should_notify is True

    out_rule = RuleSnapshot(
        rule_id=5,
        rule_type=AlertRuleType.OUT_OF_STOCK,
        threshold_value=None,
        cooldown_minutes=0,
        condition_active=False,
        last_triggered_at=None,
        last_triggered_price=None,
    )
    dec_out = evaluate_alert_rule(
        out_rule,
        _ctx(prev_avail=AvailabilityStatus.IN_STOCK, curr_avail=AvailabilityStatus.OUT_OF_STOCK),
    )
    assert dec_out.should_notify is True

    err_rule = RuleSnapshot(
        rule_id=6,
        rule_type=AlertRuleType.MONITORING_ERROR,
        threshold_value=Decimal("2"),
        cooldown_minutes=0,
        condition_active=False,
        last_triggered_at=None,
        last_triggered_price=None,
    )
    # 1 błąd < próg 2
    assert (
        evaluate_alert_rule(
            err_rule, _ctx(ok=False, failures=1, error_code=ErrorCode.BLOCKED_WAF)
        ).should_notify
        is False
    )
    # 2 błędy >= próg 2
    assert (
        evaluate_alert_rule(
            err_rule, _ctx(ok=False, failures=2, error_code=ErrorCode.BLOCKED_WAF)
        ).should_notify
        is True
    )


def test_format_telegram_message_contains_all_required_fields() -> None:
    ctx = _ctx(previous_price="2500.00", current_price="2150.00")
    rule = RuleSnapshot(
        rule_id=1,
        rule_type=AlertRuleType.TARGET_PRICE_BELOW,
        threshold_value=Decimal("2200.00"),
        cooldown_minutes=0,
        condition_active=False,
        last_triggered_at=None,
        last_triggered_price=None,
    )
    decision = evaluate_alert_rule(rule, ctx)
    msg = format_telegram_message(ctx, decision, display_tz=UTC)

    assert "RTX 4070 SUPER (12GB)" in msg
    assert "x-kom" in msg
    assert "2500.00 PLN" in msg
    assert "2150.00 PLN" in msg
    assert "-350.00 PLN (-14.00%)" in msg
    assert "https://www.x-kom.pl/p/12345.html" in msg
    assert "2026-11-22 19:00:00 UTC" in msg
