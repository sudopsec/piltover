from __future__ import annotations

import asyncio
import json

import pytest

from piltover.app.utils.bot_api.auth import resolve_bot_token
from piltover.app.utils.bot_api.methods import dispatch_method
from piltover.app.utils.bot_api.server import _handle_bot_api_request
from piltover.app.utils.bot_api.updates import bot_api_updates
from piltover.db.enums import PeerType
from pyrogram.utils import get_channel_id

from piltover.app.utils.bot_api.peers import bot_api_channel_chat_id
from piltover.db.models import Bot, BotCommand, Channel, Chat, ChatParticipant, Peer, User
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

        updates = await bot_api_updates.get_updates(bot.bot_id, timeout=2)
        assert len(updates) == 1
        assert updates[0]["message"]["text"] == "incoming via mtproto"
        assert updates[0]["message"]["from"]["id"] == db_user.id

        http_body = await _handle_bot_api_request(
            "GET", f"/bot{token}/getUpdates", {}, b"",
        )
        http_result = json.loads(http_body.split(b"\r\n\r\n", 1)[1])
        assert http_result["ok"] is True
        assert http_result["result"] == []

        await client.send_message("api2_test_0_bot", "/start")

        updates = await bot_api_updates.get_updates(bot.bot_id, timeout=2)
        assert len(updates) == 1
        chat_id = updates[0]["message"]["chat"]["id"]

        reply_body = await _handle_bot_api_request(
            "GET", f"/bot{token}/sendmessage?chat_id={chat_id}&text=reply", {}, b"",
        )
        reply_result = json.loads(reply_body.split(b"\r\n\r\n", 1)[1])
        assert reply_result["ok"] is True, reply_result
        assert reply_result["result"]["text"] == "reply"
        assert reply_result["result"]["chat"]["id"] == chat_id

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

        from piltover.db.models import MessageRef

        message = await MessageRef.get(id=sent["result"]["message_id"]).select_related("content")
        assert message.content.edit_hide is True


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

        incoming = await bot_api_updates.get_updates(bot.bot_id, timeout=2)
        assert len(incoming) == 1
        src_id = incoming[0]["message"]["message_id"]

        forwarded = await dispatch_method(
            bot, bot_user, "forwardMessage",
            {"chat_id": db_user.id, "from_chat_id": db_user.id, "message_id": src_id},
        )
        assert forwarded["ok"] is True
        assert forwarded["result"]["text"] == "forward me"
        assert "forward_origin" in forwarded["result"]


@pytest.mark.asyncio
async def test_bot_api_group_command_update(app_server) -> None:
    bot_api_updates.delete_webhook(0, drop_pending_updates=True)

    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="api8_")
        bot_user = await User.get(id=bot.bot_id)
        bot_api_updates.delete_webhook(bot.bot_id, drop_pending_updates=True)

        group = await client.create_group("bot api group", [])
        chat = await Chat.get(id=Chat.norm_id(abs(group.id)))
        await ChatParticipant.create(
            user_id=bot_user.id, chat_id=chat.id, chat_channel_id=chat.make_id(),
        )
        await Peer.get_or_create(owner_id=bot_user.id, chat_id=chat.id, type=PeerType.CHAT)

        await client.send_message(group.id, "/start")

        updates = await bot_api_updates.get_updates(bot.bot_id, timeout=2)
        assert len(updates) == 1
        assert updates[0]["message"]["text"] == "/start"
        assert updates[0]["message"]["chat"]["type"] == "group"

        bot_api_updates.set_can_read_all_group_messages(bot.bot_id, True)
        await client.send_message(group.id, "hello group")
        updates = await bot_api_updates.get_updates(bot.bot_id, timeout=2)
        assert len(updates) == 1
        assert updates[0]["message"]["text"] == "hello group"


@pytest.mark.asyncio
async def test_bot_api_send_message_supergroup_pyrogram_chat_id(app_server) -> None:
    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="api9_")
        bot_user = await User.get(id=bot.bot_id)

        group = await client.create_supergroup("bot api supergroup")
        db_channel = await Channel.get(id=Channel.norm_id(get_channel_id(group.id)))
        await ChatParticipant.create(
            user_id=bot_user.id,
            channel_id=db_channel.id,
            chat_channel_id=db_channel.make_id(),
        )

        pyrogram_chat_id = group.id
        assert pyrogram_chat_id == bot_api_channel_chat_id(db_channel.id)

        send_result = await dispatch_method(
            bot, bot_user, "sendMessage",
            {"chat_id": pyrogram_chat_id, "text": "hello supergroup"},
        )
        assert send_result["ok"] is True, send_result
        assert send_result["result"]["text"] == "hello supergroup"
        assert send_result["result"]["chat"]["id"] == pyrogram_chat_id


@pytest.mark.asyncio
async def test_bot_api_send_message_reply_parameters(app_server) -> None:
    bot_api_updates.delete_webhook(0, drop_pending_updates=True)

    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="api11_")
        bot_user = await User.get(id=bot.bot_id)
        token = f"{bot.bot_id}:{bot.token_nonce}"

        await client.send_message("api11_test_0_bot", "reply to me")
        incoming = await bot_api_updates.get_updates(bot.bot_id, timeout=2)
        assert len(incoming) == 1
        src_id = incoming[0]["message"]["message_id"]

        json_payload = json.dumps({
            "chat_id": db_user.id,
            "text": "json reply",
            "reply_parameters": {"message_id": src_id},
        }).encode()
        json_body = await _handle_bot_api_request(
            "POST",
            f"/bot{token}/sendMessage",
            {
                "content-type": "application/json",
                "content-length": str(len(json_payload)),
            },
            json_payload,
        )
        json_result = json.loads(json_body.split(b"\r\n\r\n", 1)[1])
        assert json_result["ok"] is True, json_result
        assert json_result["result"]["text"] == "json reply"
        assert json_result["result"]["reply_parameters"]["message_id"] == src_id
        assert json_result["result"]["reply_to_message"]["message_id"] == src_id

        form_payload = f"chat_id={db_user.id}&text=form+reply&reply_parameters[message_id]={src_id}".encode()
        form_body = await _handle_bot_api_request(
            "POST",
            f"/bot{token}/sendMessage",
            {
                "content-type": "application/x-www-form-urlencoded",
                "content-length": str(len(form_payload)),
            },
            form_payload,
        )
        form_result = json.loads(form_body.split(b"\r\n\r\n", 1)[1])
        assert form_result["ok"] is True, form_result
        assert form_result["result"]["text"] == "form reply"
        assert form_result["result"]["reply_parameters"]["message_id"] == src_id


@pytest.mark.asyncio
async def test_bot_api_send_message_unknown_user(app_server) -> None:
    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="api10_")
        bot_user = await User.get(id=bot.bot_id)

        result = await dispatch_method(
            bot, bot_user, "sendMessage",
            {"chat_id": 1, "text": "test simple"},
        )
        assert result["ok"] is False
        assert "chat not found" in result["description"].lower()


@pytest.mark.asyncio
async def test_bot_api_send_dice(app_server) -> None:
    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="api12_")
        bot_user = await User.get(id=bot.bot_id)

        result = await dispatch_method(
            bot, bot_user, "sendDice",
            {"chat_id": db_user.id},
        )
        assert result["ok"] is True, result
        assert "text" not in result["result"]
        assert result["result"]["dice"]["emoji"] == "🎲"
        assert 1 <= result["result"]["dice"]["value"] <= 6

        slot = await dispatch_method(
            bot, bot_user, "sendDice",
            {"chat_id": db_user.id, "emoji": "🎰"},
        )
        assert slot["ok"] is True, slot
        assert slot["result"]["dice"]["emoji"] == "🎰"
        assert 1 <= slot["result"]["dice"]["value"] <= 64

        invalid = await dispatch_method(
            bot, bot_user, "sendDice",
            {"chat_id": db_user.id, "emoji": "😀"},
        )
        assert invalid["ok"] is False
        assert "dice" in invalid["description"].lower()