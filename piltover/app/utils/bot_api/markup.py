from __future__ import annotations

import json
from typing import Any

from piltover.exceptions import ErrorRpc
from piltover.tl import (
    KeyboardButtonCallback, KeyboardButtonCopy, KeyboardButtonRow, KeyboardButtonUrl,
    ReplyInlineMarkup,
)
from piltover.tl.base import ReplyMarkup


def _parse_json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def parse_reply_markup(value: Any) -> ReplyMarkup | None:
    if value is None:
        return None

    markup = _parse_json_value(value)
    if not isinstance(markup, dict):
        raise ErrorRpc(error_code=400, error_message="REPLY_MARKUP_INVALID")

    inline_keyboard = markup.get("inline_keyboard")
    if inline_keyboard is None:
        raise ErrorRpc(error_code=400, error_message="REPLY_MARKUP_INVALID")

    rows: list[KeyboardButtonRow] = []
    for row in inline_keyboard:
        if not isinstance(row, list):
            continue
        buttons = []
        for button in row:
            if not isinstance(button, dict):
                continue
            text = str(button.get("text", ""))
            if not text:
                continue
            if "url" in button:
                buttons.append(KeyboardButtonUrl(text=text, url=str(button["url"])))
            elif "callback_data" in button:
                data = button["callback_data"]
                if isinstance(data, str):
                    data = data.encode("utf-8")
                buttons.append(KeyboardButtonCallback(text=text, data=data))
            elif "copy_text" in button:
                buttons.append(KeyboardButtonCopy(text=text, copy_text=str(button["copy_text"])))
            else:
                raise ErrorRpc(error_code=400, error_message="BUTTON_TYPE_INVALID")
        if buttons:
            rows.append(KeyboardButtonRow(buttons=buttons))

    if not rows:
        raise ErrorRpc(error_code=400, error_message="REPLY_MARKUP_INVALID")

    return ReplyInlineMarkup(rows=rows)