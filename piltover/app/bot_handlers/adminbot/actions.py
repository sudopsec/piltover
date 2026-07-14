from __future__ import annotations

import piltover.app.utils.updates_manager as upd
from piltover.app.bot_handlers.adminbot import pages
from piltover.app.bot_handlers.adminbot.callback_data import encode_stars_wait_data
from piltover.app.utils import verification
from piltover.app.utils.admin_sessions import kick_all_user_sessions
from piltover.app.utils.admin_users import LastAdminError, set_user_admin
from piltover.app.utils.spam_block import set_user_spam_blocked
from piltover.app.utils.stars_manager import grant_stars, set_stars_balance
from piltover.db.enums import AdminBotState
from piltover.db.models import AdminBotUserState, Channel, Chat, MessageRef, Peer, User
from piltover.tl.types.messages import BotCallbackAnswer


async def toggle_user_admin(
        peer: Peer, menu: MessageRef, user_id: int, admin: bool, *, list_key: str,
) -> BotCallbackAnswer:
    user = await User.get_or_none(id=user_id, bot=False, system=False, deleted=False)
    if user is None:
        return BotCallbackAnswer(message="User not found.", alert=True, cache_time=0)

    try:
        changed = await set_user_admin(user, admin)
    except LastAdminError as exc:
        return BotCallbackAnswer(message=str(exc), alert=True, cache_time=0)

    await pages.page_user(peer, user_id, menu, list_key=list_key)
    if not changed:
        return BotCallbackAnswer(message="Already up to date.", cache_time=0)
    action = "granted" if admin else "revoked"
    return BotCallbackAnswer(message=f"Admin access {action}.", cache_time=0)


async def toggle_user_verified(
        peer: Peer, menu: MessageRef, user_id: int, verified: bool, *, list_key: str,
) -> BotCallbackAnswer:
    user = await User.get_or_none(id=user_id, deleted=False)
    if user is None or user.bot or user.system:
        return BotCallbackAnswer(message="User not found.", alert=True, cache_time=0)

    changed = await verification.set_user_verified(user, verified)
    await pages.page_user(peer, user_id, menu, list_key=list_key)
    if not changed:
        return BotCallbackAnswer(message="Already up to date.", cache_time=0)
    action = "granted" if verified else "removed"
    return BotCallbackAnswer(message=f"Checkmark {action}.", cache_time=0)


async def toggle_user_spam_block(
        peer: Peer, menu: MessageRef, user_id: int, blocked: bool, *, list_key: str,
) -> BotCallbackAnswer:
    user = await User.get_or_none(id=user_id, bot=False, system=False, deleted=False)
    if user is None:
        return BotCallbackAnswer(message="User not found.", alert=True, cache_time=0)

    changed = await set_user_spam_blocked(user, blocked)
    await pages.page_user(peer, user_id, menu, list_key=list_key)
    if not changed:
        return BotCallbackAnswer(message="Already up to date.", cache_time=0)
    action = "applied" if blocked else "removed"
    return BotCallbackAnswer(message=f"Spam block {action}.", cache_time=0)


async def kick_user_sessions_action(
        peer: Peer, menu: MessageRef, user_id: int, *, list_key: str,
) -> BotCallbackAnswer:
    user = await User.get_or_none(id=user_id, bot=False, system=False, deleted=False)
    if user is None:
        return BotCallbackAnswer(message="User not found.", alert=True, cache_time=0)

    count = await kick_all_user_sessions(user.id)
    await pages.page_user_sessions(peer, user_id, menu, list_key=list_key)
    if count == 0:
        return BotCallbackAnswer(message="No sessions to kick.", cache_time=0)
    return BotCallbackAnswer(message=f"Kicked {count} session(s).", cache_time=0)


async def toggle_channel_verified(peer: Peer, menu: MessageRef, channel_id: int, verified: bool) -> BotCallbackAnswer:
    channel = await Channel.get_or_none(id=channel_id, deleted=False)
    if channel is None:
        return BotCallbackAnswer(message="Channel not found.", alert=True, cache_time=0)

    changed = await verification.set_channel_verified(channel, verified)
    await pages.page_channels(peer, 0, menu)
    if not changed:
        return BotCallbackAnswer(message="Already up to date.", cache_time=0)
    action = "granted" if verified else "removed"
    return BotCallbackAnswer(message=f"Checkmark {action}.", cache_time=0)


async def toggle_chat_verified(peer: Peer, menu: MessageRef, chat_id: int, verified: bool) -> BotCallbackAnswer:
    chat = await Chat.get_or_none(id=chat_id, deleted=False, migrated=False)
    if chat is None:
        return BotCallbackAnswer(message="Group not found.", alert=True, cache_time=0)

    changed = await verification.set_chat_verified(chat, verified)
    await pages.page_groups(peer, 0, menu)
    if not changed:
        return BotCallbackAnswer(message="Already up to date.", cache_time=0)
    action = "granted" if verified else "removed"
    return BotCallbackAnswer(message=f"Checkmark {action}.", cache_time=0)


async def grant_user_stars(
        peer: Peer, menu: MessageRef, user_id: int, amount: int, *, list_key: str,
) -> BotCallbackAnswer:
    user = await User.get_or_none(id=user_id, bot=False, system=False, deleted=False)
    if user is None:
        return BotCallbackAnswer(message="User not found.", alert=True, cache_time=0)

    balance = await grant_stars(
        user_id,
        amount,
        title="Admin grant",
        description=f"Granted {amount} stars via @admin",
    )
    await upd.update_stars_balance(user_id, balance.to_stars_amount())
    await pages.page_user_stars(peer, user_id, menu, list_key=list_key)
    return BotCallbackAnswer(message=f"Granted {amount} stars.", cache_time=0)


async def set_user_stars(
        peer: Peer, menu: MessageRef, user_id: int, amount: int, *, list_key: str,
) -> BotCallbackAnswer:
    user = await User.get_or_none(id=user_id, bot=False, system=False, deleted=False)
    if user is None:
        return BotCallbackAnswer(message="User not found.", alert=True, cache_time=0)

    balance = await set_stars_balance(
        user_id,
        amount,
        title="Admin set",
        description=f"Balance set to {amount} stars via @admin",
    )
    await upd.update_stars_balance(user_id, balance.to_stars_amount())
    await pages.page_user_stars(peer, user_id, menu, list_key=list_key)
    return BotCallbackAnswer(message=f"Balance set to {amount} stars.", cache_time=0)


async def begin_custom_stars_input(
        peer: Peer, menu: MessageRef, user_id: int, *, list_key: str, admin_user_id: int,
) -> BotCallbackAnswer:
    user = await User.get_or_none(id=user_id, bot=False, system=False, deleted=False)
    if user is None:
        return BotCallbackAnswer(message="User not found.", alert=True, cache_time=0)

    await AdminBotUserState.set_state(
        admin_user_id,
        AdminBotState.WAIT_STARS_AMOUNT,
        encode_stars_wait_data(user_id, list_key),
    )
    await pages.page_user_stars(peer, user_id, menu, list_key=list_key)
    return BotCallbackAnswer(
        message="Send the desired star balance as a number in chat.",
        alert=True,
        cache_time=0,
    )