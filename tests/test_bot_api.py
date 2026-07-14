from __future__ import annotations

import asyncio
import json

import pytest

from piltover.app.utils.bot_api.auth import resolve_bot_token
from piltover.app.utils.bot_api.methods import dispatch_method
from piltover.app.utils.bot_api.server import _handle_bot_api_request
from piltover.app.utils.bot_api.updates import bot_api_updates
from piltover.db.models import Bot, User
from tests.client import TestClient
from tests.test_bots import _create_bots


@pytest.mark.asyncio
async def test_bot_api_get_me(app_server) -> None:
    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="api_")

        result = await dispatch_method(bot, await User.get(id=bot.bot_id), "getMe", {})
        assert result["ok"] is True
        assert result["result"]["is_bot"] is True
        assert result["result"]["username"] == "api_test_0_bot"
        assert result["result"]["can_join_groups"] is True


@pytest.mark.asyncio
async def test_bot_api_send_message_and_get_updates(app_server) -> None:
    bot_api_updates.delete_webhook(0, drop_pending_updates=True)

    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="api2_")
        bot_user = await User.get(id=bot.bot_id)
        token = f"{bot.bot_id}:{bot.token_nonce}"

        send_result = await dispatch_method(
            bot, bot_user, "sendMessage",
            {"chat_id": db_user.id, "text": "hello from http api"},
        )
        assert send_result["ok"] is True
        assert send_result["result"]["text"] == "hello from http api"
        assert send_result["result"]["chat"]["id"] == db_user.id

        await client.send_message("api2_test_0_bot", "incoming via mtproto")

        updates = await bot_api_updates.get_updates(bot.bot_id, timeout=0)
        assert len(updates) == 1
        assert updates[0]["message"]["text"] == "incoming via mtproto"
        assert updates[0]["message"]["from"]["id"] == db_user.id

        http_body = await _handle_bot_api_request(
            "GET", f"/bot{token}/getUpdates", {}, b"",
        )
        http_result = json.loads(http_body.split(b"\r\n\r\n", 1)[1])
        assert http_result["ok"] is True
        assert http_result["result"] == []

        resolved = await resolve_bot_token(token)
        assert resolved is not None

        bad_body = await _handle_bot_api_request(
            "GET", "/bot0:invalid/getMe", {}, b"",
        )
        bad_result = json.loads(bad_body.split(b"\r\n\r\n", 1)[1])
        assert bad_result["ok"] is False
        assert bad_result["error_code"] == 401


@pytest.mark.asyncio
async def test_bot_api_webhook_blocks_get_updates(app_server) -> None:
    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="api3_")
        bot_user = await User.get(id=bot.bot_id)

        set_result = await dispatch_method(
            bot, bot_user, "setWebhook", {"url": "https://example.com/hook"},
        )
        assert set_result["ok"] is True

        info = await dispatch_method(bot, bot_user, "getWebhookInfo", {})
        assert info["ok"] is True
        assert info["result"]["url"] == "https://example.com/hook"

        conflict = await dispatch_method(bot, bot_user, "getUpdates", {})
        assert conflict["ok"] is False
        assert conflict["error_code"] == 409

        delete_result = await dispatch_method(bot, bot_user, "deleteWebhook", {})
        assert delete_result["ok"] is True

        updates = await dispatch_method(bot, bot_user, "getUpdates", {})
        assert updates["ok"] is True


@pytest.mark.asyncio
async def test_bot_api_get_chat_and_edit_message(app_server) -> None:
    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="api4_")
        bot_user = await User.get(id=bot.bot_id)

        chat = await dispatch_method(bot, bot_user, "getChat", {"chat_id": db_user.id})
        assert chat["ok"] is True
        assert chat["result"]["type"] == "private"
        assert chat["result"]["id"] == db_user.id

        sent = await dispatch_method(
            bot, bot_user, "sendMessage",
            {"chat_id": db_user.id, "text": "editable"},
        )
        assert sent["ok"] is True

        edited = await dispatch_method(
            bot, bot_user, "editMessageText",
            {"chat_id": db_user.id, "message_id": sent["result"]["message_id"], "text": "edited"},
        )
        assert edited["ok"] is True
        assert edited["result"]["text"] == "edited"