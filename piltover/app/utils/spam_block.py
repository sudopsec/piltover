from __future__ import annotations

from typing import TYPE_CHECKING

from piltover.db.enums import PeerType
from piltover.db.models import MessageRef, User
from piltover.exceptions import ErrorRpc

if TYPE_CHECKING:
    from piltover.db.models import Peer


async def user_spam_blocked(user: User) -> bool:
    if hasattr(user, "spam_blocked"):
        return user.spam_blocked
    values = await User.filter(id=user.id).limit(1).values_list("spam_blocked", flat=True)
    return bool(values[0] if values else False)


async def set_user_spam_blocked(user: User, blocked: bool) -> bool:
    if await user_spam_blocked(user) == blocked:
        return False

    user.spam_blocked = blocked
    await user.save(update_fields=["spam_blocked"])
    await user.inc_version()
    import piltover.app.utils.updates_manager as upd
    await upd.update_user(user)
    return True


async def _peer_allows_spam_blocked_send(peer: Peer, user_id: int) -> bool:
    if peer.type is PeerType.CHAT:
        await peer.fetch_related("chat")
        participant = await peer.chat.get_participant(user_id)
        if participant is None:
            return False
        return peer.chat.creator_id == user_id or participant.is_admin

    if peer.type is PeerType.CHANNEL:
        await peer.fetch_related("channel")
        participant = await peer.channel.get_participant(user_id)
        if participant is None:
            return False
        return peer.channel.creator_id == user_id or participant.is_admin

    return False


async def check_spam_blocked_creation(user: User) -> None:
    if user.bot or not await user_spam_blocked(user):
        return
    raise ErrorRpc(error_code=403, error_message="USER_RESTRICTED")


async def check_user_spam_blocked(
        user: User, peer: Peer | None = None, *, reply_to_message_id: int | None = None,
) -> None:
    if user.bot or not await user_spam_blocked(user):
        return

    if peer is not None:
        if peer.type is PeerType.USER:
            await peer.fetch_related("user")
            if peer.user.bot:
                return

        if peer.type in (PeerType.CHAT, PeerType.CHANNEL):
            if await _peer_allows_spam_blocked_send(peer, user.id):
                return

        if reply_to_message_id is not None:
            reply_to = await MessageRef.get_or_none(
                peer=peer, id=reply_to_message_id,
            ).select_related("content")
            if reply_to is not None and reply_to.content.author_id != user.id:
                return

    raise ErrorRpc(error_code=403, error_message="USER_RESTRICTED")