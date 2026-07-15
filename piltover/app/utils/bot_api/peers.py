from __future__ import annotations

from typing import Any

from piltover.db.enums import PeerType
from piltover.db.models import Channel, Chat, ChatParticipant, Peer, User

BOT_API_CHANNEL_OFFSET = 1_000_000_000_000


def bot_api_channel_chat_id(channel_id: int) -> int:
    return -(BOT_API_CHANNEL_OFFSET + Channel.make_id_from(channel_id))


def bot_api_channel_internal_id_candidates(chat_id: int) -> list[int]:
    raw = abs(chat_id) - BOT_API_CHANNEL_OFFSET
    candidates = [raw]
    if raw % 2 == 1:
        normalized = Channel.norm_id(raw)
        if normalized not in candidates:
            candidates.append(normalized)
    return candidates


def peer_to_bot_api_chat_id(peer: Peer) -> int:
    if peer.type is PeerType.USER:
        return peer.user_id
    if peer.type is PeerType.CHAT:
        return -Chat.make_id_from(peer.chat_id)
    if peer.type is PeerType.CHANNEL:
        return bot_api_channel_chat_id(peer.channel_id)
    raise ValueError(f"unsupported peer type: {peer.type}")


def bot_api_chat_id_to_peer_type(chat_id: int) -> PeerType:
    if chat_id > 0:
        return PeerType.USER
    if chat_id <= -BOT_API_CHANNEL_OFFSET:
        return PeerType.CHANNEL
    return PeerType.CHAT


def _parse_bot_api_chat_id(chat_id: Any) -> int | None:
    if isinstance(chat_id, bool):
        return None
    if isinstance(chat_id, int):
        return chat_id
    if not isinstance(chat_id, str):
        return None

    value = chat_id.strip()
    if not value.lstrip("-").isdigit():
        return None
    return int(value)


async def resolve_bot_api_peer(bot_user: User, chat_id: Any) -> Peer | None:
    chat_id = _parse_bot_api_chat_id(chat_id)
    if chat_id is None:
        return None

    peer_type = bot_api_chat_id_to_peer_type(chat_id)

    if peer_type is PeerType.USER:
        if not await User.filter(id=chat_id, deleted=False).exists():
            return None
        return await Peer.get_or_create_for_user(
            bot_user.id, chat_id, select_related=("user", "user__username"),
        )

    if peer_type is PeerType.CHAT:
        internal_id = Chat.norm_id(abs(chat_id))
        if not await ChatParticipant.filter(
                user_id=bot_user.id, chat_id=internal_id, left=False,
        ).exists():
            return None
        peer, _ = await Peer.get_or_create(
            owner_id=bot_user.id, chat_id=internal_id, type=PeerType.CHAT,
        )
        await peer.fetch_related("chat", "chat__username")
        return peer

    for internal_channel_id in bot_api_channel_internal_id_candidates(chat_id):
        if not await ChatParticipant.filter(
                user_id=bot_user.id, channel_id=internal_channel_id, left=False,
        ).exists():
            continue
        peer = await Peer.get_or_none(
            channel_id=internal_channel_id, owner_id__isnull=True, channel__deleted=False,
        ).select_related("channel", "channel__username")
        if peer is not None:
            return peer
    return None


async def peer_is_writable(bot_user: User, peer: Peer) -> bool:
    if peer.type is PeerType.USER:
        return True
    if peer.type is PeerType.CHAT:
        participant = await peer.chat.get_participant(bot_user.id)
        return participant is not None and peer.chat.can_send_plain(participant)
    if peer.type is PeerType.CHANNEL:
        participant = await peer.channel.get_participant(bot_user.id)
        return participant is not None and peer.channel.can_send_messages(participant)
    return False