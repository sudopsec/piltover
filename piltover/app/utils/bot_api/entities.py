from __future__ import annotations

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


def parse_entities_param(value: Any, *, field_name: str = "entities") -> list[dict] | None:
    from piltover.app.utils.bot_api.params import parse_json_array

    return parse_json_array(value, field_name=field_name)


def _entity_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"entities: invalid {field_name}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"entities: invalid {field_name}") from exc


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
            "offset": _entity_int(entity.get("offset"), field_name="offset"),
            "length": _entity_int(entity.get("length"), field_name="length"),
        }
        if entity_type == "text_link":
            if "url" not in entity:
                raise ValueError("entities: text_link requires url")
            kwargs["url"] = str(entity["url"])
        elif entity_type == "text_mention":
            user = entity.get("user") or {}
            kwargs["user_id"] = _entity_int(
                user.get("id", entity.get("user_id")),
                field_name="user_id",
            )
        elif entity_type == "pre" and (language := entity.get("language")):
            kwargs["language"] = str(language)
        elif entity_type == "custom_emoji":
            kwargs["document_id"] = _entity_int(
                entity.get("custom_emoji_id", entity.get("document_id")),
                field_name="custom_emoji_id",
            )

        result.append(tl_cls(**kwargs))

    return result