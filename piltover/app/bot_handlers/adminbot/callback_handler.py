from __future__ import annotations

from piltover.app.bot_handlers.adminbot import actions, pages
from piltover.app.bot_handlers.adminbot.callback_data import split_list_key
from piltover.db.models import MessageRef, Peer
from piltover.tl.types.messages import BotCallbackAnswer


def _parse_user_page(data: bytes) -> tuple[int, str]:
    body, list_key = split_list_key(data)
    text = body.decode()
    if text.startswith("adm:user:stars:"):
        return int(text.split(":")[3]), list_key
    if text.startswith("adm:user:sess:"):
        return int(text.split(":")[3]), list_key
    if text.startswith("adm:user:mem:"):
        return int(text.split(":")[3]), list_key
    if text.startswith("adm:user:"):
        parts = text.split(":")
        return int(parts[2]), list_key
    raise ValueError(f"Unexpected user page callback: {text}")


async def adminbot_callback_query_handler(
        peer: Peer, message: MessageRef, data: bytes,
) -> BotCallbackAnswer | None:
    if data == b"adm:home":
        await pages.page_home(peer, message)
        return BotCallbackAnswer(cache_time=0)

    if data == b"adm:stats":
        await pages.page_stats(peer, message)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:users:"):
        await pages.page_users(peer, int(data[10:]), message)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:admins:"):
        await pages.page_admins(peer, int(data[11:]), message)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:channels:"):
        await pages.page_channels(peer, int(data[13:]), message)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:groups:"):
        await pages.page_groups(peer, int(data[11:]), message)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:user:stars:"):
        user_id, list_key = _parse_user_page(data)
        await pages.page_user_stars(peer, user_id, message, list_key=list_key)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:user:sess:"):
        user_id, list_key = _parse_user_page(data)
        await pages.page_user_sessions(peer, user_id, message, list_key=list_key)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:user:mem:"):
        body, list_key = split_list_key(data)
        parts = body.decode().split(":")
        user_id = int(parts[3])
        page = int(parts[4])
        await pages.page_user_memberships(peer, user_id, page, message, list_key=list_key)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:user:"):
        user_id, list_key = _parse_user_page(data)
        await pages.page_user(peer, user_id, message, list_key=list_key)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:act:stars:add:"):
        body, list_key = split_list_key(data)
        _, _, _, _, user_part, amount_part = body.decode().split(":", 5)
        return await actions.grant_user_stars(peer, message, int(user_part), int(amount_part), list_key=list_key)

    if data.startswith(b"adm:act:stars:set:"):
        body, list_key = split_list_key(data)
        _, _, _, _, user_part, amount_part = body.decode().split(":", 5)
        return await actions.set_user_stars(peer, message, int(user_part), int(amount_part), list_key=list_key)

    if data.startswith(b"adm:act:stars:custom:"):
        body, list_key = split_list_key(data)
        user_id = int(body.decode().split(":")[4])
        return await actions.begin_custom_stars_input(
            peer, message, user_id, list_key=list_key, admin_user_id=peer.owner_id,
        )

    if data.startswith(b"adm:act:admin:"):
        body, list_key = split_list_key(data)
        return await actions.toggle_user_admin(peer, message, int(body.decode().split(":")[3]), True, list_key=list_key)

    if data.startswith(b"adm:act:unadmin:"):
        body, list_key = split_list_key(data)
        return await actions.toggle_user_admin(peer, message, int(body.decode().split(":")[3]), False, list_key=list_key)

    if data.startswith(b"adm:act:verify:"):
        body, list_key = split_list_key(data)
        return await actions.toggle_user_verified(peer, message, int(body.decode().split(":")[3]), True, list_key=list_key)

    if data.startswith(b"adm:act:unverify:"):
        body, list_key = split_list_key(data)
        return await actions.toggle_user_verified(peer, message, int(body.decode().split(":")[3]), False, list_key=list_key)

    if data.startswith(b"adm:act:spam:"):
        body, list_key = split_list_key(data)
        return await actions.toggle_user_spam_block(peer, message, int(body.decode().split(":")[3]), True, list_key=list_key)

    if data.startswith(b"adm:act:unspam:"):
        body, list_key = split_list_key(data)
        return await actions.toggle_user_spam_block(peer, message, int(body.decode().split(":")[3]), False, list_key=list_key)

    if data.startswith(b"adm:act:kick:"):
        body, list_key = split_list_key(data)
        return await actions.kick_user_sessions_action(peer, message, int(body.decode().split(":")[3]), list_key=list_key)

    if data.startswith(b"adm:act:v:ch:"):
        return await actions.toggle_channel_verified(peer, message, int(data[13:]), True)

    if data.startswith(b"adm:act:uv:ch:"):
        return await actions.toggle_channel_verified(peer, message, int(data[14:]), False)

    if data.startswith(b"adm:act:v:g:"):
        return await actions.toggle_chat_verified(peer, message, int(data[12:]), True)

    if data.startswith(b"adm:act:uv:g:"):
        return await actions.toggle_chat_verified(peer, message, int(data[13:]), False)

    return None