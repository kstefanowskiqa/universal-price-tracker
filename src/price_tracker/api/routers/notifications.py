from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from price_tracker.api.schemas import (
    AlertRuleCreateRequest,
    AlertRuleResponse,
    AlertRuleUpdateRequest,
    NotificationChannelResponse,
    NotificationLogResponse,
    TelegramChannelConfigRequest,
    TelegramTestResponse,
)
from price_tracker.api.security import require_api_token
from price_tracker.core.enums import NotificationDeliveryStatus
from price_tracker.notifications.service import AlertRuleView, ChannelView, NotificationService

router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_api_token)],
    tags=["notifications"],
)


def _get_notifications(request: Request) -> NotificationService:
    svc: NotificationService = request.app.state.notification_service
    return svc


def _map_rule(view: AlertRuleView) -> AlertRuleResponse:
    r = view.rule
    return AlertRuleResponse(
        id=r.id,
        offer_id=r.offer_id,
        product_id=r.product_id,
        product_name=view.product_name,
        store_name=view.store_name,
        variant_label=view.variant_label,
        rule_type=r.rule_type,
        threshold_value=r.threshold_value,
        is_enabled=r.is_enabled,
        cooldown_minutes=r.cooldown_minutes,
        condition_active=r.condition_active,
        last_triggered_at=r.last_triggered_at,
        last_triggered_price=r.last_triggered_price,
    )


def _map_channel(view: ChannelView) -> NotificationChannelResponse:
    ch = view.channel
    return NotificationChannelResponse(
        id=ch.id,
        channel_type=ch.channel_type,
        name=ch.name,
        config_json=ch.config_json,
        is_enabled=ch.is_enabled,
        has_secret=view.has_secret,
    )


@router.get("/alerts", response_model=list[AlertRuleResponse])
async def list_alert_rules(
    service: Annotated[NotificationService, Depends(_get_notifications)],
    offer_id: Annotated[int | None, Query()] = None,
    product_id: Annotated[int | None, Query()] = None,
) -> list[AlertRuleResponse]:
    views = await service.list_alert_rules(offer_id=offer_id, product_id=product_id)
    return [_map_rule(v) for v in views]


@router.post(
    "/alerts",
    response_model=AlertRuleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_alert_rule(
    payload: AlertRuleCreateRequest,
    service: Annotated[NotificationService, Depends(_get_notifications)],
) -> AlertRuleResponse:
    view = await service.create_alert_rule(
        rule_type=payload.rule_type,
        offer_id=payload.offer_id,
        product_id=payload.product_id,
        threshold_value=payload.threshold_value,
        cooldown_minutes=payload.cooldown_minutes,
        is_enabled=payload.is_enabled,
    )
    return _map_rule(view)


@router.patch("/alerts/{rule_id}", response_model=AlertRuleResponse)
async def update_alert_rule(
    rule_id: int,
    payload: AlertRuleUpdateRequest,
    service: Annotated[NotificationService, Depends(_get_notifications)],
) -> AlertRuleResponse:
    view = await service.update_alert_rule(
        rule_id,
        threshold_value=payload.threshold_value,
        is_enabled=payload.is_enabled,
        cooldown_minutes=payload.cooldown_minutes,
        reset_state=payload.reset_state,
    )
    return _map_rule(view)


@router.delete("/alerts/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert_rule(
    rule_id: int,
    service: Annotated[NotificationService, Depends(_get_notifications)],
) -> Response:
    await service.delete_alert_rule(rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/notifications/channels", response_model=list[NotificationChannelResponse])
async def list_channels(
    service: Annotated[NotificationService, Depends(_get_notifications)],
) -> list[NotificationChannelResponse]:
    views = await service.list_channels()
    return [_map_channel(v) for v in views]


@router.put("/notifications/channels/telegram", response_model=NotificationChannelResponse)
async def configure_telegram(
    payload: TelegramChannelConfigRequest,
    service: Annotated[NotificationService, Depends(_get_notifications)],
) -> NotificationChannelResponse:
    view = await service.configure_telegram_channel(
        chat_id=payload.chat_id,
        bot_token=payload.bot_token,
        name=payload.name,
        is_enabled=payload.is_enabled,
        disable_web_page_preview=payload.disable_web_page_preview,
    )
    return _map_channel(view)


@router.post("/notifications/channels/telegram/test", response_model=TelegramTestResponse)
async def test_telegram(
    service: Annotated[NotificationService, Depends(_get_notifications)],
) -> TelegramTestResponse:
    res = await service.send_telegram_test_message()
    return TelegramTestResponse(
        ok=res.ok,
        provider_message_id=res.provider_message_id,
        error_message=res.error_message,
    )


@router.get("/notifications/logs", response_model=list[NotificationLogResponse])
async def list_notification_logs(
    service: Annotated[NotificationService, Depends(_get_notifications)],
    offer_id: Annotated[int | None, Query()] = None,
    delivery_status: Annotated[NotificationDeliveryStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=300)] = 100,
) -> list[NotificationLogResponse]:
    logs = await service.list_notification_logs(
        offer_id=offer_id,
        status=delivery_status,
        limit=limit,
    )
    return [
        NotificationLogResponse(
            id=item.id,
            alert_rule_id=item.alert_rule_id,
            offer_id=item.offer_id,
            channel_id=item.channel_id,
            dedup_key=item.dedup_key,
            status=item.status,
            payload_summary=item.payload_summary,
            error_message=item.error_message,
            created_at=item.created_at,
        )
        for item in logs
    ]
