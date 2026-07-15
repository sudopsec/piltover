from __future__ import annotations

import json
from typing import Any

from piltover.db.models import User
from piltover.tl import (
    MessageEntityBold, MessageEntityBotCommand, MessageEntityCashtag, MessageEntityCode,
    MessageEntityCustomEmoji, MessageEntityEmail, MessageEntityHashtag, MessageEntityItalic,
    MessageEntityMention, MessageEntityMentionName, MessageEntityPhone, MessageEntityPre,
    MessageEntitySpoiler, MessageEntityStrike, MessageEntityTextUrl, MessageEntityUnderline,
    MessageEntityUrl, MessageEntityBlockquote,
)
from piltover.tl.base import MessageEntity as TLMessageEntityBase

_TL_TO_BOT_API: dict[int, str] = {
    MessageEntityMention.tlid(): "mention",
    MessageEntityHashtag.tlid(): "hashtag",
    MessageEntityBotCommand.tlid(): "bot_command",
    MessageEntityUrl.tlid(): "url",
    MessageEntityEmail.tlid(): "email",
    MessageEntityBold.tlid(): "bold",
    MessageEntityItalic.tlid(): "italic",
    MessageEntityCode.tlid(): "code",
    MessageEntityPre.tlid(): "pre",
    MessageEntityTextUrl.tlid(): "text_link",
    MessageEntityMentionName.tlid(): "text_mention",
    MessageEntityPhone.tlid(): "phone_number",
    MessageEntityCashtag.tlid(): "cashtag",
    MessageEntityUnderline.tlid(): "underline",
    MessageEntityStrike.tlid(): "strikethrough",
    MessageEntitySpoiler.tlid(): "spoiler",
    MessageEntityBlockquote.tlid(): "blockquote",
    MessageEntityCustomEmoji.tlid(): "custom_emoji",
}

_BOT_API_TO_TL: dict[str, type[TLMessageEntityBase]] = {
    "mention": MessageEntityMention,
    "hashtag": MessageEntityHashtag,
    "bot_command": MessageEntityBotCommand,
    "url": MessageEntityUrl,
    "email": MessageEntityEmail,
    "bold": MessageEntityBold,
    "italic": MessageEntityItalic,
    "code": MessageEntityCode,
    "pre": MessageEntityPre,
    "text_link": MessageEntityTextUrl,
    "text_mention": MessageEntityMentionName,
    "phone_number": MessageEntityPhone,
    "cashtag": MessageEntityCashtag,
    "underline": MessageEntityUnderline,
    "strikethrough": MessageEntityStrike,
    "spoiler": MessageEntitySpoiler,
    "blockquote": MessageEntityBlockquote,
    "custom_emoji": MessageEntityCustomEmoji,
}


async def entities_to_bot_api(entities: list[dict] | None) -> list[dict] | None:
    if not entities:
        return None

    result: list[dict] = []
    for entity in entities:
        tl_id = entity.get("_")
        entity_type = _TL_TO_BOT_API.get(tl_id)
        if entity_type is None:
            continue

        item: dict[str, Any] = {
            "type": entity_type,
            "offset": entity["offset"],
            "length": entity["length"],
        }
        if entity_type == "text_link":
            item["url"] = entity["url"]
        elif entity_type == "text_mention":
            user = await User.get(id=entity["user_id"])
            user_item: dict[str, Any] = {
                "id": user.id,
                "is_bot": user.bot,
                "first_name": user.first_name,
            }
            if username := await user.get_raw_username():
                user_item["username"] = username
            item["user"] = user_item
        elif entity_type == "pre":
            if language := entity.get("language"):
                item["language"] = language
        elif entity_type == "custom_emoji":
            item["custom_emoji_id"] = str(entity["document_id"])

        result.append(item)

    return result or None


def _parse_entities_param(value: Any) -> list[dict] | None:
    if value is None:
        return None
    if isinstance(value, str):
        parsed = json.loads(value)
        if not isinstance(parsed, list):
            raise ValueError("entities must be a JSON array")
        return parsed
    if isinstance(value, list):
        return value
    raise ValueError("entities must be a JSON array")


def bot_api_entities_to_tl(entities: list[dict]) -> list[TLMessageEntityBase]:
    result: list[TLMessageEntityBase] = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_type = entity.get("type")
        tl_cls = _BOT_API_TO_TL.get(entity_type)
        if tl_cls is None:
            continue

        kwargs: dict[str, Any] = {
            "offset": int(entity["offset"]),
            "length": int(entity["length"]),
        }
        if entity_type == "text_link":
            kwargs["url"] = str(entity["url"])
        elif entity_type == "text_mention":
            user = entity.get("user") or {}
            kwargs["user_id"] = int(user.get("id", entity.get("user_id", 0)))
        elif entity_type == "pre" and (language := entity.get("language")):
            kwargs["language"] = str(language)
        elif entity_type == "custom_emoji":
            kwargs["document_id"] = int(entity.get("custom_emoji_id", entity.get("document_id", 0)))

        result.append(tl_cls(**kwargs))

    return result