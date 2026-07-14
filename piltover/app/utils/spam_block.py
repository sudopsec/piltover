from __future__ import annotations

from typing import TYPE_CHECKING

from piltover.db.enums import PeerType
from piltover.db.models import MessageRef
from piltover.exceptions import ErrorRpc

if TYPE_CHECKING:
    from piltover.db.models import Peer, User


async def set_user_spam_blocked(user: User, blocked: bool) -> bool:
    if user.spam_blocked == blocked:
        return False

    user.spam_blocked = blocked
    await user.save(update_fields=["spam_blocked"])
    await user.inc_version()
    import piltover.app.utils.updates_manager as upd
    await upd.update_user(user)
    return True


async def check_user_spam_blocked(
        user: User, peer: Peer | None = None, *, reply_to_message_id: int | None = None,
) -> None:
    if user.bot or not getattr(user, "spam_blocked", False):
        return

    if peer is not None:
        if peer.type is PeerType.USER:
            await peer.fetch_related("user")
            if peer.user.bot:
                return

        if reply_to_message_id is not None:
            reply_to = await MessageRef.get_or_none(
                peer=peer, id=reply_to_message_id,
            ).select_related("content")
            if reply_to is not None and reply_to.content.author_id != user.id:
                return

    raise ErrorRpc(error_code=403, error_message="USER_RESTRICTED")