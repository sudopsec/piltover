from __future__ import annotations

from typing import TYPE_CHECKING

import piltover.app.utils.updates_manager as upd

if TYPE_CHECKING:
    from piltover.db.models import User, Chat, Channel


async def set_user_support(user: User, support: bool) -> bool:
    if user.support == support:
        return False

    from piltover.cache import Cache
    from piltover.db.models import State

    user.support = support
    await user.save(update_fields=["support"])
    await user.inc_version()
    await Cache.obj.delete(user._cache_key())
    await State.get_or_create(user=user, defaults={"pts": 0})
    await upd.update_user(user)
    return True


async def set_user_verified(user: User, verified: bool) -> bool:
    if user.verified == verified:
        return False

    from piltover.cache import Cache
    from piltover.db.models import State

    user.verified = verified
    await user.save(update_fields=["verified"])
    await Cache.obj.delete(user._cache_key())
    await user.inc_version()
    await State.get_or_create(user=user, defaults={"pts": 0})
    await upd.update_user(user)
    return True


async def set_chat_verified(chat: Chat, verified: bool) -> bool:
    if chat.verified == verified:
        return False

    chat.verified = verified
    chat.version += 1
    await chat.save(update_fields=["verified", "version"])
    await upd.update_chat(chat)
    return True


async def set_channel_verified(channel: Channel, verified: bool) -> bool:
    if channel.verified == verified:
        return False

    channel.verified = verified
    channel.version += 1
    await channel.save(update_fields=["verified", "version"])
    await upd.update_channel(channel)
    return True