from __future__ import annotations

from typing import TYPE_CHECKING

import piltover.app.utils.updates_manager as upd
from piltover.db.enums import PeerType
from piltover.db.models import Username
from piltover.exceptions import ErrorRpc

if TYPE_CHECKING:
    from piltover.db.models import Peer, User

SPAMBOT_USERNAME = "spambot"


async def set_user_spam_blocked(user: User, blocked: bool) -> bool:
    if user.spam_blocked == blocked:
        return False

    user.spam_blocked = blocked
    await user.save(update_fields=["spam_blocked"])
    await user.inc_version()
    await upd.update_user(user)
    return True


async def _peer_username(peer: Peer) -> str | None:
    if peer.type is not PeerType.USER:
        return None
    if isinstance(peer.user.username, Username):
        return peer.user.username.username
    return await peer.user.get_raw_username()


async def check_user_spam_blocked(user: User, peer: Peer | None = None) -> None:
    if user.bot or not getattr(user, "spam_blocked", False):
        return

    if peer is not None:
        username = await _peer_username(peer)
        if username == SPAMBOT_USERNAME:
            return

    raise ErrorRpc(error_code=403, error_message="USER_RESTRICTED")