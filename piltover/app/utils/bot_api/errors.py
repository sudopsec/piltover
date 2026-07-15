from __future__ import annotations

import json
import re
from typing import Any

from tortoise.exceptions import DoesNotExist, IntegrityError

from piltover.app.utils.bot_api.response import api_error
from piltover.app.utils.bot_api.updates import _BotApiConflict
from piltover.exceptions import ErrorRpc

_SLOWMODE_RE = re.compile(r"^SLOWMODE_WAIT_(\d+)$")
_FLOOD_WAIT_RE = re.compile(r"^FLOOD_WAIT_(\d+)$")

_RPC_DESCRIPTIONS: dict[str, str] = {
    "PEER_ID_INVALID": "Bad Request: chat not found",
    "USER_ID_INVALID": "Bad Request: chat not found",
    "INPUT_USER_DEACTIVATED": "Bad Request: chat not found",
    "USER_IS_BLOCKED": "Forbidden: bot was blocked by the user",
    "YOU_BLOCKED_USER": "Forbidden: bot can't initiate conversation with a user",
    "CHAT_WRITE_FORBIDDEN": "Forbidden: bot is not allowed to send messages in this chat",
    "CHAT_SEND_PLAIN_FORBIDDEN": "Forbidden: bot is not allowed to send messages in this chat",
    "MESSAGE_ID_INVALID": "Bad Request: message not found",
    "MESSAGE_NOT_MODIFIED": "Bad Request: message is not modified",
    "MESSAGE_EMPTY": "Bad Request: message text is empty",
    "MESSAGE_TOO_LONG": "Bad Request: message is too long",
    "MEDIA_CAPTION_TOO_LONG": "Bad Request: message caption is too long",
    "REPLY_MARKUP_INVALID": "Bad Request: can't parse reply keyboard markup JSON object",
    "BUTTON_TYPE_INVALID": "Bad Request: can't parse inline keyboard button",
    "BUTTON_DATA_INVALID": "Bad Request: button callback data is invalid",
    "REPLY_TO_INVALID": "Bad Request: replied message not found",
    "MEDIA_INVALID": "Bad Request: wrong file identifier/HTTP url specified",
    "MEDIA_EMPTY": "Bad Request: there is no media in the message to edit",
    "MEDIA_NEW_INVALID": "Bad Request: new media content is invalid",
    "MEDIA_PREV_INVALID": "Bad Request: previous media content is invalid",
    "INPUT_FILE_INVALID": "Bad Request: wrong file identifier/HTTP url specified",
    "PEER_FLOOD": "Forbidden: too many requests",
    "USER_RESTRICTED": "Forbidden: user is restricted",
    "SCHEDULE_BOT_NOT_ALLOWED": "Bad Request: bots can't send scheduled messages",
    "BOT_ONESIDE_NOT_AVAIL": "Bad Request: bots can't send messages to other bots",
    "USER_IS_BOT": "Bad Request: bots can't send messages to bots",
    "CHAT_FORWARDS_RESTRICTED": "Bad Request: message can't be forwarded",
    "CHAT_RESTRICTED": "Forbidden: bot is not allowed to send messages in this chat",
    "PIN_RESTRICTED": "Forbidden: bot is not allowed to pin messages",
    "MESSAGE_AUTHOR_REQUIRED": "Forbidden: bot is not allowed to edit this message",
}


def _description_prefix(error_code: int) -> str:
    if error_code == 403:
        return "Forbidden"
    if error_code == 404:
        return "Not Found"
    if error_code == 409:
        return "Conflict"
    if error_code in (420, 429):
        return "Too Many Requests"
    if error_code >= 500:
        return "Internal Server Error"
    return "Bad Request"


def _normalize_description(description: str, error_code: int) -> str:
    if description.startswith(("Bad Request:", "Forbidden:", "Conflict:", "Not Found:", "Too Many Requests:", "Internal Server Error:")):
        return description
    if description.startswith("Conflict:"):
        return description
    return f"{_description_prefix(error_code)}: {description}"


def rpc_error_to_api_error(exc: ErrorRpc) -> dict[str, Any]:
    message = exc.error_message

    if message.startswith(("Bad Request:", "Forbidden:", "Conflict:", "Not Found:", "Too Many Requests:", "Internal Server Error:")):
        return api_error(message, error_code=exc.error_code)

    slowmode = _SLOWMODE_RE.match(message)
    if slowmode is not None:
        retry_after = int(slowmode.group(1))
        return api_error(
            f"Too Many Requests: retry after {retry_after}",
            error_code=429,
            parameters={"retry_after": retry_after},
        )

    flood_wait = _FLOOD_WAIT_RE.match(message)
    if flood_wait is not None:
        retry_after = int(flood_wait.group(1))
        return api_error(
            f"Too Many Requests: retry after {retry_after}",
            error_code=429,
            parameters={"retry_after": retry_after},
        )

    mapped = _RPC_DESCRIPTIONS.get(message)
    if mapped is not None:
        return api_error(mapped, error_code=exc.error_code)

    return api_error(
        _normalize_description(message.replace("_", " ").lower(), exc.error_code),
        error_code=exc.error_code,
    )


def translate_exception(exc: BaseException) -> dict[str, Any] | None:
    if isinstance(exc, ErrorRpc):
        return rpc_error_to_api_error(exc)

    if isinstance(exc, _BotApiConflict):
        text = str(exc).strip() or "can't use getUpdates while webhook is active"
        if not text.startswith("Conflict:"):
            text = f"Conflict: {text}"
        return api_error(text, error_code=409)

    if isinstance(exc, DoesNotExist):
        return api_error("Bad Request: chat not found")

    if isinstance(exc, IntegrityError):
        return api_error("Bad Request: chat not found")

    if isinstance(exc, json.JSONDecodeError):
        return api_error("Bad Request: can't parse request body as JSON")

    if isinstance(exc, ValueError):
        return api_error(f"Bad Request: {exc}")

    if isinstance(exc, (TypeError, KeyError, AttributeError, IndexError)):
        return api_error(f"Bad Request: {exc}")

    return None