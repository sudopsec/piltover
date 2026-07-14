from __future__ import annotations

from piltover.db.models import CallbackQuery, BotPrecheckoutQuery, MessageRef, Peer, User


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
        result["can_join_groups"] = True
        result["can_read_all_group_messages"] = False
        if username:
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


async def message_to_bot_api(bot_user: User, peer: Peer, message: MessageRef) -> dict:
    content = message.content
    author = await User.get(id=content.author_id) if content.author_id is not None else None

    result: dict = {
        "message_id": message.id,
        "date": int(content.date.timestamp()),
        "chat": await private_chat_to_bot_api(peer),
    }

    if author is not None:
        result["from"] = await user_to_bot_api(author)

    if content.message:
        result["text"] = content.message

    if content.edit_date is not None:
        result["edit_date"] = int(content.edit_date.timestamp())

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