from __future__ import annotations

import asyncio
import json

import pytest

from piltover.app.utils.bot_api.auth import resolve_bot_token
from piltover.app.utils.bot_api.methods import dispatch_method
from piltover.app.utils.bot_api.server import _handle_bot_api_request
from piltover.app.utils.bot_api.updates import bot_api_updates
from piltover.db.models import Bot, BotCommand, User
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
            bot, bot_user, "setWebhook",
            {"url": "https://example.com/hook", "secret_token": "test-secret"},
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


@pytest.mark.asyncio
async def test_bot_api_reply_markup_and_commands(app_server) -> None:
    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="api5_")
        bot_user = await User.get(id=bot.bot_id)

        set_cmds = await dispatch_method(
            bot, bot_user, "setMyCommands",
            {"commands": json.dumps([{"command": "start", "description": "Start bot"}])},
        )
        assert set_cmds["ok"] is True

        get_cmds = await dispatch_method(bot, bot_user, "getMyCommands", {})
        assert get_cmds["ok"] is True
        assert get_cmds["result"][0]["command"] == "start"

        sent = await dispatch_method(
            bot, bot_user, "sendMessage",
            {
                "chat_id": db_user.id,
                "text": "pick one",
                "reply_markup": json.dumps({
                    "inline_keyboard": [[{"text": "OK", "callback_data": "ok"}]],
                }),
            },
        )
        assert sent["ok"] is True
        assert sent["result"]["reply_markup"]["inline_keyboard"][0][0]["text"] == "OK"

        await BotCommand.filter(bot_id=bot_user.id).delete()


@pytest.mark.asyncio
async def test_bot_api_send_chat_action(app_server) -> None:
    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="api6_")
        bot_user = await User.get(id=bot.bot_id)

        result = await dispatch_method(
            bot, bot_user, "sendChatAction",
            {"chat_id": db_user.id, "action": "typing"},
        )
        assert result["ok"] is True


@pytest.mark.asyncio
async def test_bot_api_forward_message(app_server) -> None:
    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="api7_")
        bot_user = await User.get(id=bot.bot_id)
        bot_api_updates.delete_webhook(bot.bot_id, drop_pending_updates=True)

        await client.send_message("api7_test_0_bot", "forward me")

        incoming = await bot_api_updates.get_updates(bot.bot_id, timeout=0)
        assert len(incoming) == 1
        src_id = incoming[0]["message"]["message_id"]

        forwarded = await dispatch_method(
            bot, bot_user, "forwardMessage",
            {"chat_id": db_user.id, "from_chat_id": db_user.id, "message_id": src_id},
        )
        assert forwarded["ok"] is True
        assert forwarded["result"]["text"] == "forward me"
        assert "forward_origin" in forwarded["result"]