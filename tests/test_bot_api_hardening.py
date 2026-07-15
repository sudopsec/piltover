from __future__ import annotations

import asyncio
import json
import time

import pytest

from piltover.app.utils.bot_api.errors import translate_exception
from piltover.app.utils.bot_api.markup import parse_reply_markup
from piltover.app.utils.bot_api.methods import dispatch_method
from piltover.app.utils.bot_api.params import finalize_bot_api_params
from piltover.db.models import User
from piltover.exceptions import ErrorRpc
from piltover.tl import ReplyKeyboardForceReply, ReplyKeyboardHide, ReplyKeyboardMarkup
from tests.client import TestClient
from tests.test_bots import _create_bots


@pytest.mark.asyncio
async def test_send_to_bot_returns_before_bot_api_notify(app_server, monkeypatch) -> None:
    from piltover.app.utils.bot_api import groups

    release = asyncio.Event()

    async def slow_notify(*args, **kwargs) -> None:
        await release.wait()

    monkeypatch.setattr(groups, "notify_bot_api_recipients", slow_notify)

    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="defer_")

        started = time.monotonic()
        await client.send_message("defer_test_0_bot", "keyboard tap")
        elapsed = time.monotonic() - started

        assert elapsed < 1.0
        release.set()
        await asyncio.sleep(0)


def test_parse_reply_markup_empty_inline_keyboard_returns_none() -> None:
    assert parse_reply_markup({"inline_keyboard": []}) is None


def test_parse_reply_keyboard_simple() -> None:
    markup = parse_reply_markup({
        "keyboard": [[{"text": "A"}, {"text": "B"}]],
    })
    assert isinstance(markup, ReplyKeyboardMarkup)
    assert len(markup.rows) == 1
    assert markup.rows[0].buttons[0].text == "A"


def test_parse_reply_keyboard_with_resize() -> None:
    markup = parse_reply_markup({
        "keyboard": [[{"text": "Hello"}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    })
    assert isinstance(markup, ReplyKeyboardMarkup)
    assert markup.resize is True
    assert markup.single_use is True


def test_parse_remove_keyboard() -> None:
    markup = parse_reply_markup({"remove_keyboard": True})
    assert isinstance(markup, ReplyKeyboardHide)


def test_parse_force_reply() -> None:
    markup = parse_reply_markup({
        "force_reply": True,
        "input_field_placeholder": "Type here",
    })
    assert isinstance(markup, ReplyKeyboardForceReply)
    assert markup.placeholder == "Type here"


def test_parse_reply_markup_callback_data_too_long() -> None:
    with pytest.raises(ErrorRpc) as exc:
        parse_reply_markup({
            "inline_keyboard": [[{"text": "X", "callback_data": "x" * 65}]],
        })
    assert exc.value.error_message == "BUTTON_DATA_INVALID"


def test_parse_reply_markup_switch_inline_and_web_app() -> None:
    markup = parse_reply_markup({
        "inline_keyboard": [[
            {"text": "Inline", "switch_inline_query": "q"},
            {"text": "App", "web_app": {"url": "https://example.com"}},
        ]],
    })
    assert markup is not None
    assert len(markup.rows) == 1
    assert len(markup.rows[0].buttons) == 2


def test_parse_reply_markup_invalid_json_object() -> None:
    with pytest.raises(ErrorRpc):
        parse_reply_markup("{not json")


def test_finalize_invalid_reply_markup_json() -> None:
    with pytest.raises(ValueError, match="reply_markup"):
        finalize_bot_api_params({"reply_markup": "{bad"})


def test_translate_type_error() -> None:
    result = translate_exception(TypeError("bad type"))
    assert result is not None
    assert result["ok"] is False
    assert result["error_code"] == 400


@pytest.mark.asyncio
async def test_send_message_oversized_callback_data(app_server) -> None:
    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="hard1_")
        bot_user = await User.get(id=bot.bot_id)

        result = await dispatch_method(
            bot, bot_user, "sendMessage",
            {
                "chat_id": db_user.id,
                "text": "kb",
                "reply_markup": json.dumps({
                    "inline_keyboard": [[{"text": "X", "callback_data": "y" * 100}]],
                }),
            },
        )
        assert result["ok"] is False
        assert "callback data" in result["description"].lower()
        assert result["error_code"] == 400


@pytest.mark.asyncio
async def test_edit_message_reply_markup_clear_keyboard(app_server) -> None:
    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="hard2_")
        bot_user = await User.get(id=bot.bot_id)

        sent = await dispatch_method(
            bot, bot_user, "sendMessage",
            {
                "chat_id": db_user.id,
                "text": "with kb",
                "reply_markup": json.dumps({
                    "inline_keyboard": [[{"text": "OK", "callback_data": "ok"}]],
                }),
            },
        )
        assert sent["ok"] is True
        assert sent["result"]["reply_markup"] is not None

        cleared = await dispatch_method(
            bot, bot_user, "editMessageReplyMarkup",
            {
                "chat_id": db_user.id,
                "message_id": sent["result"]["message_id"],
                "reply_markup": json.dumps({"inline_keyboard": []}),
            },
        )
        assert cleared["ok"] is True
        assert cleared["result"].get("reply_markup") is None


@pytest.mark.asyncio
async def test_send_message_reply_keyboard(app_server) -> None:
    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="hard4_")
        bot_user = await User.get(id=bot.bot_id)

        result = await dispatch_method(
            bot, bot_user, "sendMessage",
            {
                "chat_id": db_user.id,
                "text": "pick",
                "reply_markup": {
                    "keyboard": [[{"text": "A"}, {"text": "B"}]],
                    "resize_keyboard": True,
                },
            },
        )
        assert result["ok"] is True, result
        assert "reply_markup" not in result["result"]


@pytest.mark.asyncio
async def test_send_message_remove_keyboard(app_server) -> None:
    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="hard5_")
        bot_user = await User.get(id=bot.bot_id)

        result = await dispatch_method(
            bot, bot_user, "sendMessage",
            {
                "chat_id": db_user.id,
                "text": "dismiss",
                "reply_markup": json.dumps({"remove_keyboard": True}),
            },
        )
        assert result["ok"] is True, result
        assert "reply_markup" not in result["result"]


@pytest.mark.asyncio
async def test_invalid_message_id_returns_400(app_server) -> None:
    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="hard3_")
        bot_user = await User.get(id=bot.bot_id)

        result = await dispatch_method(
            bot, bot_user, "deleteMessage",
            {"chat_id": db_user.id, "message_id": "not-a-number"},
        )
        assert result["ok"] is False
        assert result["error_code"] == 400
        assert "integer" in result["description"].lower()