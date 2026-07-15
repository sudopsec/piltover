from __future__ import annotations

import json

import pytest

from piltover.app.utils.bot_api.errors import rpc_error_to_api_error, translate_exception
from piltover.app.utils.bot_api.methods import dispatch_method
from piltover.exceptions import ErrorRpc
from piltover.db.models import User
from tests.client import TestClient
from tests.test_bots import _create_bots


def test_rpc_error_maps_peer_id_invalid() -> None:
    result = rpc_error_to_api_error(ErrorRpc(400, "PEER_ID_INVALID"))
    assert result["ok"] is False
    assert result["description"] == "Bad Request: chat not found"
    assert result["error_code"] == 400


def test_rpc_error_maps_user_blocked() -> None:
    result = rpc_error_to_api_error(ErrorRpc(400, "USER_IS_BLOCKED"))
    assert result["description"] == "Forbidden: bot was blocked by the user"


def test_rpc_error_maps_slowmode() -> None:
    result = rpc_error_to_api_error(ErrorRpc(420, "SLOWMODE_WAIT_12"))
    assert result["error_code"] == 429
    assert "retry after 12" in result["description"]
    assert result["parameters"] == {"retry_after": 12}


def test_translate_integrity_error() -> None:
    from tortoise.exceptions import IntegrityError

    result = translate_exception(IntegrityError("fk failed"))
    assert result is not None
    assert result["description"] == "Bad Request: chat not found"


@pytest.mark.asyncio
async def test_bot_api_invalid_json_body(app_server) -> None:
    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="apierr_")
        token = f"{bot.bot_id}:{bot.token_nonce}"

        from piltover.app.utils.bot_api.server import _handle_bot_api_request

        body = await _handle_bot_api_request(
            "POST",
            f"/bot{token}/sendMessage",
            {"content-type": "application/json", "content-length": "7"},
            b"{bad}",
        )
        result = json.loads(body.split(b"\r\n\r\n", 1)[1])
        assert result["ok"] is False
        assert "json" in result["description"].lower()
        assert result["error_code"] == 400


@pytest.mark.asyncio
async def test_bot_api_invalid_reply_markup(app_server) -> None:
    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="apierr2_")
        bot_user = await User.get(id=bot.bot_id)

        result = await dispatch_method(
            bot, bot_user, "sendMessage",
            {
                "chat_id": db_user.id,
                "text": "bad keyboard",
                "reply_markup": json.dumps({"inline_keyboard": [[{"text": "X"}]]}),
            },
        )
        assert result["ok"] is False
        assert "inline keyboard button" in result["description"].lower()
        assert result["error_code"] == 400


@pytest.mark.asyncio
async def test_bot_api_unknown_user_returns_chat_not_found(app_server) -> None:
    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="apierr3_")
        bot_user = await User.get(id=bot.bot_id)

        result = await dispatch_method(
            bot, bot_user, "sendMessage",
            {"chat_id": 1, "text": "hello"},
        )
        assert result["ok"] is False
        assert result["description"] == "Bad Request: chat not found"
        assert result["error_code"] == 400