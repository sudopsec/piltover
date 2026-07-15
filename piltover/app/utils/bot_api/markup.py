from __future__ import annotations

from typing import Any

from piltover.exceptions import ErrorRpc
from piltover.tl import (
    KeyboardButton, KeyboardButtonBuy, KeyboardButtonCallback, KeyboardButtonCopy,
    KeyboardButtonGame, KeyboardButtonRequestGeoLocation, KeyboardButtonRequestPhone,
    KeyboardButtonRequestPoll, KeyboardButtonRow, KeyboardButtonSwitchInline,
    KeyboardButtonUrl, KeyboardButtonWebView, ReplyInlineMarkup, ReplyKeyboardForceReply,
    ReplyKeyboardHide, ReplyKeyboardMarkup,
)
from piltover.tl.base import ReplyMarkup

_MAX_CALLBACK_DATA_BYTES = 64
_MAX_SWITCH_INLINE_QUERY_LEN = 256
_MAX_COPY_TEXT_LEN = 256

_INLINE_ONLY_BUTTON_KEYS = frozenset({
    "url", "callback_data", "copy_text", "switch_inline_query",
    "switch_inline_query_current_chat", "callback_game", "pay", "web_app",
})


def _markup_error() -> ErrorRpc:
    return ErrorRpc(error_code=400, error_message="REPLY_MARKUP_INVALID")


def _button_error() -> ErrorRpc:
    return ErrorRpc(error_code=400, error_message="BUTTON_TYPE_INVALID")


def _callback_data_error() -> ErrorRpc:
    return ErrorRpc(error_code=400, error_message="BUTTON_DATA_INVALID")


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    if isinstance(value, (int, float)):
        return value != 0
    return default


def _encode_callback_data(data: Any) -> bytes:
    if isinstance(data, bytes):
        encoded = data
    elif isinstance(data, str):
        encoded = data.encode("utf-8")
    else:
        raise _button_error()
    if not encoded or len(encoded) > _MAX_CALLBACK_DATA_BYTES:
        raise _callback_data_error()
    return encoded


def _parse_web_app_url(button: dict[str, Any]) -> str:
    web_app = button.get("web_app")
    if not isinstance(web_app, dict):
        raise _button_error()
    url = web_app.get("url")
    if not url:
        raise _button_error()
    return str(url)


def _parse_reply_button(button: dict[str, Any]) -> Any:
    text = str(button.get("text", "")).strip()
    if not text:
        raise _button_error()

    if button.get("request_contact") is True:
        return KeyboardButtonRequestPhone(text=text)
    if button.get("request_location") is True:
        return KeyboardButtonRequestGeoLocation(text=text)
    if "request_poll" in button:
        poll = button["request_poll"]
        quiz = isinstance(poll, dict) and poll.get("type") == "quiz"
        return KeyboardButtonRequestPoll(text=text, quiz=quiz)
    if any(key in button for key in _INLINE_ONLY_BUTTON_KEYS):
        raise _button_error()

    return KeyboardButton(text=text)


def _parse_inline_button(button: dict[str, Any]) -> Any:
    text = str(button.get("text", "")).strip()
    if not text:
        raise _button_error()

    if "url" in button:
        return KeyboardButtonUrl(text=text, url=str(button["url"]))
    if "callback_data" in button:
        return KeyboardButtonCallback(text=text, data=_encode_callback_data(button["callback_data"]))
    if "copy_text" in button:
        copy_text = str(button["copy_text"])
        if not copy_text or len(copy_text) > _MAX_COPY_TEXT_LEN:
            raise _callback_data_error()
        return KeyboardButtonCopy(text=text, copy_text=copy_text)
    if "switch_inline_query_current_chat" in button:
        query = str(button["switch_inline_query_current_chat"])
        if len(query) > _MAX_SWITCH_INLINE_QUERY_LEN:
            raise _callback_data_error()
        return KeyboardButtonSwitchInline(text=text, query=query, same_peer=True)
    if "switch_inline_query" in button:
        query = str(button["switch_inline_query"])
        if len(query) > _MAX_SWITCH_INLINE_QUERY_LEN:
            raise _callback_data_error()
        return KeyboardButtonSwitchInline(text=text, query=query, same_peer=False)
    if "callback_game" in button:
        return KeyboardButtonGame(text=text)
    if button.get("pay") is True:
        return KeyboardButtonBuy(text=text)
    if "web_app" in button:
        return KeyboardButtonWebView(text=text, url=_parse_web_app_url(button))

    raise _button_error()


def _parse_keyboard_rows(
        keyboard: Any,
        *,
        parse_button,
) -> list[KeyboardButtonRow]:
    if not isinstance(keyboard, list):
        raise _markup_error()
    if not keyboard:
        raise _markup_error()

    rows: list[KeyboardButtonRow] = []
    for row in keyboard:
        if not isinstance(row, list):
            raise _markup_error()
        if not row:
            continue
        buttons = []
        for button in row:
            if not isinstance(button, dict):
                raise _button_error()
            buttons.append(parse_button(button))
        if buttons:
            rows.append(KeyboardButtonRow(buttons=buttons))

    if not rows:
        raise _markup_error()
    return rows


def _parse_reply_keyboard_markup(markup: dict[str, Any]) -> ReplyKeyboardMarkup:
    keyboard = markup.get("keyboard")
    rows = _parse_keyboard_rows(keyboard, parse_button=_parse_reply_button)
    placeholder = markup.get("input_field_placeholder")
    return ReplyKeyboardMarkup(
        rows=rows,
        resize=_parse_bool(markup.get("resize_keyboard")),
        single_use=_parse_bool(markup.get("one_time_keyboard")),
        selective=_parse_bool(markup.get("selective")),
        persistent=_parse_bool(markup.get("is_persistent")),
        placeholder=str(placeholder) if placeholder else None,
    )


def _parse_inline_keyboard_markup(markup: dict[str, Any]) -> ReplyMarkup | None:
    inline_keyboard = markup.get("inline_keyboard")
    if inline_keyboard is None:
        raise _markup_error()
    if not isinstance(inline_keyboard, list):
        raise _markup_error()
    if not inline_keyboard:
        return None
    rows = _parse_keyboard_rows(inline_keyboard, parse_button=_parse_inline_button)
    return ReplyInlineMarkup(rows=rows)


def parse_reply_markup(value: Any) -> ReplyMarkup | None:
    if value is None:
        return None

    from piltover.app.utils.bot_api.params import parse_json_object

    try:
        markup = parse_json_object(value, field_name="reply_markup")
    except (ValueError, TypeError) as exc:
        raise _markup_error() from exc

    if not isinstance(markup, dict):
        raise _markup_error()

    if _parse_bool(markup.get("remove_keyboard")):
        return ReplyKeyboardHide(selective=_parse_bool(markup.get("selective")))

    if _parse_bool(markup.get("force_reply")):
        placeholder = markup.get("input_field_placeholder")
        return ReplyKeyboardForceReply(
            single_use=_parse_bool(markup.get("one_time_keyboard")),
            selective=_parse_bool(markup.get("selective")),
            placeholder=str(placeholder) if placeholder else None,
        )

    if "inline_keyboard" in markup:
        return _parse_inline_keyboard_markup(markup)

    if "keyboard" in markup:
        return _parse_reply_keyboard_markup(markup)

    raise _markup_error()