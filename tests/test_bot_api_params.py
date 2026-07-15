import json

from piltover.app.utils.bot_api.params import finalize_bot_api_params, normalize_nested_params, parse_query_value
from piltover.app.utils.bot_api.parse_mode import parse_html, parse_markdown_v2
from piltover.app.utils.bot_api.reply import parse_reply_parameters


def test_normalize_nested_reply_parameters() -> None:
    params = normalize_nested_params({
        "chat_id": "123",
        "text": "hi",
        "reply_parameters[message_id]": "42",
        "reply_parameters[chat_id]": "123",
    })
    assert params["reply_parameters"] == {"message_id": 42, "chat_id": 123}


def test_normalize_deep_inline_keyboard() -> None:
    params = finalize_bot_api_params({
        "reply_markup[inline_keyboard][0][0][text]": "Open",
        "reply_markup[inline_keyboard][0][0][url]": "https://example.com",
    })
    assert params["reply_markup"] == {
        "inline_keyboard": [[{"text": "Open", "url": "https://example.com"}]],
    }


def test_finalize_json_query_fields() -> None:
    params = finalize_bot_api_params({
        "reply_parameters": json.dumps({"message_id": 5}),
        "entities": json.dumps([{"type": "bold", "offset": 0, "length": 3}]),
    })
    assert params["reply_parameters"] == {"message_id": 5}
    assert params["entities"][0]["type"] == "bold"


def test_parse_query_value_decodes_json_and_bool() -> None:
    assert parse_query_value("%7B%22message_id%22%3A9%7D") == {"message_id": 9}
    assert parse_query_value("true") is True
    assert parse_query_value("42") == 42


def test_parse_reply_parameters_from_legacy_field() -> None:
    assert parse_reply_parameters({"reply_to_message_id": 7}) == {"message_id": 7}


def test_parse_reply_parameters_from_object() -> None:
    assert parse_reply_parameters({
        "reply_parameters": {"message_id": 9, "allow_sending_without_reply": True},
    }) == {"message_id": 9, "allow_sending_without_reply": True}


def test_parse_html_bold_and_link() -> None:
    text, entities = parse_html('Hello <b>world</b> and <a href="https://example.com">site</a>')
    assert text == "Hello world and site"
    assert len(entities) == 2
    assert entities[0].offset == 6
    assert entities[1].url == "https://example.com"


def test_parse_markdown_v2_bold() -> None:
    text, entities = parse_markdown_v2("*bold* text")
    assert text == "bold text"
    assert len(entities) == 1