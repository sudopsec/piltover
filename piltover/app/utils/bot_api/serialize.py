from __future__ import annotations

from io import BytesIO

from piltover.app.utils.bot_api.entities import entities_to_bot_api
from piltover.app.utils.bot_api.reply import reply_parameters_to_bot_api
from piltover.app.utils.bot_api.media import file_to_bot_api, serialize_message_media_to_bot_api
from piltover.app.utils.bot_api.peers import peer_to_bot_api_chat_id
from piltover.db.enums import PeerType
from piltover.db.models import BotInfo, CallbackQuery, BotPrecheckoutQuery, MessageFwdHeader, MessageRef, Peer, User
from piltover.tl import (
    KeyboardButton, KeyboardButtonBuy, KeyboardButtonCallback, KeyboardButtonCopy,
    KeyboardButtonGame, KeyboardButtonRequestGeoLocation, KeyboardButtonRequestPhone,
    KeyboardButtonRequestPoll, KeyboardButtonSwitchInline, KeyboardButtonUrl,
    KeyboardButtonWebView, ReplyInlineMarkup,
)
from piltover.tl import TLObject


def _user_field(user: User, name: str) -> str | None:
    try:
        return getattr(user, name)
    except AttributeError:
        return None


async def user_to_bot_api(user: User, *, for_get_me: bool = False) -> dict:
    result: dict = {
        "id": user.id,
        "is_bot": user.bot,
        "first_name": user.first_name,
    }
    if last_name := _user_field(user, "last_name"):
        result["last_name"] = last_name
    username = await user.get_raw_username()
    if username:
        result["username"] = username
    if lang_code := _user_field(user, "lang_code"):
        result["language_code"] = lang_code

    if for_get_me and user.bot:
        from piltover.app.utils.bot_api.updates import bot_api_updates

        info = await BotInfo.get_or_none(user_id=user.id)
        result["can_join_groups"] = info.can_join_groups if info is not None else True
        read_all = bot_api_updates.can_read_all_group_messages(user.id)
        if info is not None and not info.group_privacy:
            read_all = True
        result["can_read_all_group_messages"] = read_all
        if info is not None and info.inline_mode:
            result["supports_inline_queries"] = True
        elif username:
            from piltover.app.bot_handlers import bots as builtin_bots
            if username in builtin_bots.INLINE_QUERY_HANDLERS:
                result["supports_inline_queries"] = True

    return result


async def private_chat_to_bot_api(peer: Peer) -> dict:
    chat: dict = {
        "id": peer.user_id,
        "type": "private",
    }
    user = await User.get(id=peer.user_id)
    if user.first_name:
        chat["first_name"] = user.first_name
    if last_name := _user_field(user, "last_name"):
        chat["last_name"] = last_name
    username = await user.get_raw_username()
    if username:
        chat["username"] = username
    return chat


async def peer_to_bot_api(peer: Peer) -> dict:
    if peer.type is PeerType.USER:
        return await private_chat_to_bot_api(peer)

    chat: dict = {
        "id": peer_to_bot_api_chat_id(peer),
    }

    if peer.type is PeerType.CHAT:
        await peer.fetch_related("chat")
        chat["type"] = "group"
        chat["title"] = peer.chat.name
        return chat

    if peer.type is PeerType.CHANNEL:
        await peer.fetch_related("channel", "channel__username")
        channel = peer.channel
        chat["type"] = "channel" if channel.channel else "supergroup"
        chat["title"] = channel.name
        if channel.username is not None:
            chat["username"] = channel.username.username
        return chat

    raise ValueError(f"unsupported peer type: {peer.type}")


def _button_to_bot_api(button) -> dict | None:
    text = getattr(button, "text", None)
    if not text:
        return None
    if isinstance(button, KeyboardButtonUrl):
        return {"text": text, "url": button.url}
    if isinstance(button, KeyboardButtonCallback):
        data = button.data
        if isinstance(data, bytes):
            data = data.decode("utf-8", errors="surrogateescape")
        return {"text": text, "callback_data": data}
    if isinstance(button, KeyboardButtonCopy):
        return {"text": text, "copy_text": button.copy_text}
    if isinstance(button, KeyboardButtonSwitchInline):
        return {"text": text, "switch_inline_query": button.query or ""}
    if isinstance(button, KeyboardButtonGame):
        return {"text": text, "callback_game": {}}
    if isinstance(button, KeyboardButtonBuy):
        return {"text": text, "pay": True}
    if isinstance(button, KeyboardButtonWebView):
        return {"text": text, "web_app": {"url": button.url}}
    if isinstance(button, KeyboardButton):
        return {"text": text}
    if isinstance(button, KeyboardButtonRequestPhone):
        return {"text": text, "request_contact": True}
    if isinstance(button, KeyboardButtonRequestGeoLocation):
        return {"text": text, "request_location": True}
    if isinstance(button, KeyboardButtonRequestPoll):
        item: dict = {"text": text, "request_poll": {}}
        if button.quiz:
            item["request_poll"] = {"type": "quiz"}
        return item
    return None


def _rows_to_bot_api(markup) -> list[list[dict]]:
    rows = []
    for row in markup.rows:
        buttons = []
        for button in row.buttons:
            if (converted := _button_to_bot_api(button)) is not None:
                buttons.append(converted)
        if buttons:
            rows.append(buttons)
    return rows


async def reply_markup_to_bot_api(reply_markup_bytes: bytes | None) -> dict | None:
    """Serialize stored markup for Bot API Message.reply_markup (inline only)."""
    if reply_markup_bytes is None:
        return None
    try:
        markup = TLObject.read(BytesIO(reply_markup_bytes))
    except Exception:
        return None

    if not isinstance(markup, ReplyInlineMarkup):
        return None

    rows = _rows_to_bot_api(markup)
    if not rows:
        return None
    return {"inline_keyboard": rows}


async def fwd_header_to_bot_api(fwd_header: MessageFwdHeader) -> dict:
    result: dict = {"date": int(fwd_header.date.timestamp())}
    if fwd_header.from_user_id is not None:
        result["from"] = await user_to_bot_api(await User.get(id=fwd_header.from_user_id))
    elif fwd_header.from_name:
        result["from"] = {"id": 0, "is_bot": False, "first_name": fwd_header.from_name}
    return result


async def message_to_bot_api(
        bot_user: User, peer: Peer, message: MessageRef, *, depth: int = 0,
) -> dict:
    content = message.content
    author = await User.get(id=content.author_id) if content.author_id is not None else None

    result: dict = {
        "message_id": message.id,
        "date": int(content.date.timestamp()),
        "chat": await peer_to_bot_api(peer),
    }

    if author is not None:
        result["from"] = await user_to_bot_api(author)

    if content.message:
        result["text"] = content.message

    if entities := await entities_to_bot_api(content.entities):
        result["entities"] = entities

    if content.edit_date is not None:
        result["edit_date"] = int(content.edit_date.timestamp())

    if content.reply_markup and (markup := await reply_markup_to_bot_api(content.reply_markup)):
        result["reply_markup"] = markup

    if depth < 1 and message.reply_to_id is not None:
        if reply_parameters := reply_parameters_to_bot_api(peer, message):
            result["reply_parameters"] = reply_parameters
        reply_to = await MessageRef.get_or_none(id=message.reply_to_id).select_related(
            "content", "content__author", "peer", "peer__user",
        )
        if reply_to is not None:
            result["reply_to_message"] = await message_to_bot_api(
                bot_user, reply_to.peer, reply_to, depth=depth + 1,
            )

    if content.fwd_header_id is not None:
        await content.fetch_related("fwd_header")
        if content.fwd_header is not None:
            result["forward_origin"] = await fwd_header_to_bot_api(content.fwd_header)

    if content.media_id is not None:
        await content.fetch_related("media", "media__file")
        if content.media is not None and (media_fields := await serialize_message_media_to_bot_api(content.media)):
            result.update(media_fields)

    return result


async def callback_query_to_bot_api(bot_user: User, query: CallbackQuery) -> dict:
    message = await MessageRef.get(id=query.message_id).select_related(
        "content", "content__author", "peer", "peer__user",
    )
    peer = await Peer.get_or_create_for_user(
        bot_user.id, query.user_id, select_related=("user", "user__username"),
    )
    return {
        "id": str(query.id),
        "from": await user_to_bot_api(await User.get(id=query.user_id)),
        "message": await message_to_bot_api(bot_user, message.peer, message),
        "chat_instance": str(query.user_id),
        "data": query.data.decode("utf-8", errors="surrogateescape"),
    }


async def pre_checkout_query_to_bot_api(query: BotPrecheckoutQuery) -> dict:
    return {
        "id": str(query.id),
        "from": await user_to_bot_api(await User.get(id=query.user_id)),
        "currency": query.currency,
        "total_amount": query.total_amount,
        "invoice_payload": query.payload.decode("utf-8", errors="surrogateescape"),
    }


async def bot_command_to_bot_api(command) -> dict:
    return {"command": command.name, "description": command.description}


async def get_file_result(file) -> dict:
    from piltover.app.utils.bot_api.media import file_unique_id

    ext = ""
    if file.filename and "." in file.filename:
        ext = file.filename.rsplit(".", 1)[-1]
    elif file.mime_type:
        ext = file.mime_type.rsplit("/", 1)[-1]
    path = f"{file.id}.{ext}" if ext else str(file.id)
    return {
        "file_id": str(file.id),
        "file_unique_id": file_unique_id(file),
        "file_size": file.size,
        "file_path": path,
    }