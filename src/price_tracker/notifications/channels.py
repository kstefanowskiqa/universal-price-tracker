from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

from price_tracker.core.enums import NotificationChannelType

_BOT_TOKEN_IN_TEXT_RE = re.compile(r"bot\d{6,15}:[A-Za-z0-9_-]{20,80}", re.IGNORECASE)


def _sanitize_error_text(text: str) -> str:
    return _BOT_TOKEN_IN_TEXT_RE.sub("bot[REDACTED]", text)


@dataclass(frozen=True, slots=True)
class ChannelDeliveryResult:
    ok: bool
    provider_message_id: str | None = None
    error_message: str | None = None


class NotificationChannelClient(ABC):
    """Rozszerzalny kontrakt kanału powiadomień (Telegram, docelowo e-mail / Discord / Webhook)."""

    channel_type: NotificationChannelType

    @abstractmethod
    async def send(
        self,
        *,
        message: str,
        config: dict[str, Any],
        secret: str | None,
    ) -> ChannelDeliveryResult:
        """Wysyła wiadomość przez dany kanał i zwraca ustrukturyzowany wynik dostarczenia."""

    async def close(self) -> None:
        return None


class TelegramChannelClient(NotificationChannelClient):
    """Lekki, asynchroniczny klient Telegram Bot API oparty o httpx."""

    channel_type = NotificationChannelType.TELEGRAM

    def __init__(
        self,
        *,
        api_base_url: str = "https://api.telegram.org",
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_base_url = api_base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=8.0),
            transport=transport,
        )

    async def send(
        self,
        *,
        message: str,
        config: dict[str, Any],
        secret: str | None,
    ) -> ChannelDeliveryResult:
        chat_id = str(config.get("chat_id") or "").strip()
        if not chat_id:
            return ChannelDeliveryResult(
                ok=False,
                error_message="Brak skonfigurowanego parametru chat_id dla kanału Telegram.",
            )
        if not secret or not secret.strip():
            return ChannelDeliveryResult(
                ok=False,
                error_message="Brak tokenu bota Telegrama w magazynie sekretów.",
            )

        endpoint = f"{self._api_base_url}/bot{secret.strip()}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": bool(config.get("disable_web_page_preview", False)),
        }

        try:
            response = await self._client.post(endpoint, json=payload)
        except httpx.TimeoutException:
            return ChannelDeliveryResult(
                ok=False,
                error_message="Przekroczono czas oczekiwania na odpowiedź Telegram Bot API.",
            )
        except httpx.HTTPError as exc:
            return ChannelDeliveryResult(
                ok=False,
                error_message=f"Błąd sieci Telegram Bot API: {_sanitize_error_text(str(exc))}",
            )

        try:
            data = response.json()
        except ValueError:
            return ChannelDeliveryResult(
                ok=False,
                error_message=(
                    f"Nieprawidłowa odpowiedź HTTP {response.status_code} z Telegram API."
                ),
            )

        if response.status_code == 200 and data.get("ok") is True:
            msg_id = data.get("result", {}).get("message_id")
            return ChannelDeliveryResult(
                ok=True,
                provider_message_id=str(msg_id) if msg_id is not None else None,
            )

        description = _sanitize_error_text(str(data.get("description") or "Nieznany błąd"))
        return ChannelDeliveryResult(
            ok=False,
            error_message=f"Telegram API (HTTP {response.status_code}): {description}",
        )

    async def close(self) -> None:
        await self._client.aclose()


class ChannelRegistry:
    """Rejestr obsługiwanych kanałów powiadomień."""

    def __init__(self) -> None:
        self._clients: dict[NotificationChannelType, NotificationChannelClient] = {}

    def register(self, client: NotificationChannelClient) -> None:
        self._clients[client.channel_type] = client

    def get(self, channel_type: NotificationChannelType) -> NotificationChannelClient | None:
        return self._clients.get(channel_type)

    async def close_all(self) -> None:
        for client in self._clients.values():
            await client.close()


def build_default_channel_registry(
    *,
    telegram_transport: httpx.AsyncBaseTransport | None = None,
) -> ChannelRegistry:
    registry = ChannelRegistry()
    registry.register(TelegramChannelClient(transport=telegram_transport))
    return registry
