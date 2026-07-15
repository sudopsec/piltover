import json

import pytest

from piltover.app.utils.bot_api.methods import dispatch_method
from piltover.app.utils.bot_api.server import _handle_bot_api_request
from piltover.app.utils.utils import _utf8_span_to_char_span, process_message_entities
from piltover.db.models import User
from tests.client import TestClient
from tests.test_bots import _create_bots


def test_utf8_span_to_char_span_with_emoji() -> None:
    text = "🌐 API: http://localhost:8081"
    byte_start = text.encode("utf-8").index(b"http")
    byte_end = len(text.encode("utf-8"))
    char_span = _utf8_span_to_char_span(text, byte_start, byte_end)
    assert char_span == (7, 28)
    assert text[char_span[0]:char_span[1]] == "http://localhost:8081"


@pytest.mark.asyncio
async def test_process_message_entities_finds_url_after_emoji() -> None:
    from piltover.tl import MessageEntityUrl

    text = "🌐 API: http://localhost:8081"
    entities = await process_message_entities(text, None, 1)
    assert entities is not None
    assert any(entity["_"] == MessageEntityUrl.tlid() for entity in entities)


@pytest.mark.asyncio
async def test_bot_api_send_message_html_parse_mode(app_server) -> None:
    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="apihtml_")
        bot_user = await User.get(id=bot.bot_id)

        result = await dispatch_method(
            bot, bot_user, "sendMessage",
            {
                "chat_id": db_user.id,
                "text": "<b>bold</b> link <a href=\"https://example.com\">ex</a>",
                "parse_mode": "HTML",
            },
        )
        assert result["ok"] is True, result
        assert result["result"]["text"] == "bold link ex"
        assert {entity["type"] for entity in result["result"]["entities"]} == {"bold", "text_link"}


@pytest.mark.asyncio
async def test_bot_api_edit_message_html_with_plain_url(app_server) -> None:
    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="apihtml3_")
        bot_user = await User.get(id=bot.bot_id)

        sent = await dispatch_method(
            bot, bot_user, "sendMessage",
            {"chat_id": db_user.id, "text": "stats"},
        )
        assert sent["ok"] is True

        edited = await dispatch_method(
            bot, bot_user, "editMessageText",
            {
                "chat_id": db_user.id,
                "message_id": sent["result"]["message_id"],
                "parse_mode": "HTML",
                "text": "📈 <b>Статистика</b>\n🌐 API: http://localhost:8081",
            },
        )
        assert edited["ok"] is True, edited
        assert "http://localhost:8081" in edited["result"]["text"]
        assert any(entity["type"] == "bold" for entity in edited["result"]["entities"])


@pytest.mark.asyncio
async def test_bot_api_send_message_html_via_get_query(app_server) -> None:
    async with TestClient(phone_number="123456789") as client:
        db_user = await User.get(phone_number=client.phone_number)
        bot, = await _create_bots(db_user, 1, username_prefix="apihtml2_")
        token = f"{bot.bot_id}:{bot.token_nonce}"

        body = await _handle_bot_api_request(
            "GET",
            (
                f"/bot{token}/sendMessage?"
                f"chat_id={db_user.id}&parse_mode=HTML&text=%3Cb%3Ehi%3C%2Fb%3E"
            ),
            {},
            b"",
        )
        result = json.loads(body.split(b"\r\n\r\n", 1)[1])
        assert result["ok"] is True, result
        assert result["result"]["text"] == "hi"
        assert result["result"]["entities"][0]["type"] == "bold"