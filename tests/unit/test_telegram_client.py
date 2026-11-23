from __future__ import annotations

import json

import httpx

from price_tracker.notifications.channels import TelegramChannelClient


async def test_telegram_client_sends_html_message_successfully() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 777}})

    client = TelegramChannelClient(transport=httpx.MockTransport(handler))
    try:
        res = await client.send(
            message="<b>Test</b>",
            config={"chat_id": "123456"},
            secret="999888777:AAH_test_secret_token_1234567890",
        )
        assert res.ok is True
        assert res.provider_message_id == "777"
        assert captured["body"] == {
            "chat_id": "123456",
            "text": "<b>Test</b>",
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
    finally:
        await client.close()


async def test_telegram_client_handles_errors_and_never_leaks_token() -> None:
    secret_token = "999888777:AAH_super_secret_token_9876543210"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"Connection failed for {request.url}")

    client = TelegramChannelClient(transport=httpx.MockTransport(handler))
    try:
        res = await client.send(
            message="Test",
            config={"chat_id": "123456"},
            secret=secret_token,
        )
        assert res.ok is False
        assert res.error_message is not None
        assert secret_token not in res.error_message
        assert "bot[REDACTED]" in res.error_message
    finally:
        await client.close()
