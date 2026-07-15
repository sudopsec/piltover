from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, UTC

from loguru import logger
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

import piltover.app.utils.updates_manager as upd
from piltover.db.enums import MessageType, PeerType
from piltover.db.models import Channel, MessageRef, Peer
from piltover.db.models.peer import PeerChannelT


@dataclass(frozen=True, slots=True)
class ReadDiscussionTarget:
    broadcast_channel: Channel
    broadcast_post_id: int
    discussion_thread_id: int


async def ensure_discussion_thread(message_id: int) -> MessageRef | None:
    created_discussion_message: MessageRef | None = None
    broadcast_post: MessageRef | None = None
    discussion_peer: PeerChannelT | None = None

    async with in_transaction():
        message = await MessageRef.select_for_update().get_or_none(id=message_id).select_related(
            *MessageRef.PREFETCH_FIELDS, "peer__channel", "content__author", "content__send_as_channel",
        )
        if message is None:
            return None

        broadcast_channel = message.peer.channel
        if not broadcast_channel.channel or broadcast_channel.is_discussion:
            return None
        if not (discussion_channel_id := broadcast_channel.discussion_id):
            return None
        if message.content.type is not MessageType.REGULAR:
            return None

        discussion_thread_id = message.discussion_id or message.discussion_top_message_id
        if discussion_thread_id is not None:
            return await MessageRef.get_or_none(id=discussion_thread_id)

        discussion_peer = await Peer.get_or_none(
            channel_id=discussion_channel_id,
        ).select_related("channel")
        if discussion_peer is None:
            logger.warning(f"Internal channel ({discussion_channel_id}) peer does not exist")
            return None

        discussion_content = await message.content.clone_discussion_mirror(
            discussion_peer, broadcast_channel.id,
        )
        created_discussion_message = await MessageRef.create(
            peer=discussion_peer,
            content=discussion_content,
            pinned=True,
            is_discussion=True,
        )
        await discussion_peer.sync_last_message()

        message.discussion = created_discussion_message
        message.discussion_top_message_id = created_discussion_message.id
        message.content.edit_date = datetime.now(UTC)
        message.content.edit_hide = True
        message.content.version += 1
        message.content.replies_version += 1
        await message.save(update_fields=["discussion_id", "discussion_top_message_id"])
        await message.content.save(update_fields=["edit_date", "edit_hide", "version", "replies_version"])
        broadcast_post = message

    if created_discussion_message is None or discussion_peer is None or broadcast_post is None:
        return None

    await upd.send_messages_channel([created_discussion_message], discussion_peer.channel)
    await upd.edit_message_channel(broadcast_post.peer.channel, broadcast_post)
    return created_discussion_message


async def _discussion_channel_peer(discussion_channel_id: int) -> Peer | None:
    return await Peer.get_or_none(
        channel_id=discussion_channel_id,
    ).select_related("channel")


async def resolve_get_replies_target(peer: Peer, msg_id: int) -> tuple[Peer, int]:
    channel = peer.channel

    if channel.channel and not channel.is_discussion:
        post = await MessageRef.get_or_none(
            Q(id=msg_id, peer__channel_id=channel.id, content__type=MessageType.REGULAR),
        ).only("id")
        if post is None or channel.discussion_id is None:
            return peer, msg_id

        discussion_message = await ensure_discussion_thread(post.id)
        if discussion_message is None:
            return peer, msg_id

        discussion_peer = await _discussion_channel_peer(channel.discussion_id)
        if discussion_peer is None:
            return peer, msg_id

        return discussion_peer, discussion_message.id

    if channel.is_discussion:
        if await MessageRef.filter(id=msg_id, peer=peer, is_discussion=True).exists():
            return peer, msg_id

        broadcast_channel = await Channel.get_or_none(discussion_id=channel.id, deleted=False)
        if broadcast_channel is None:
            return peer, msg_id

        post = await MessageRef.get_or_none(
            Q(id=msg_id, peer__channel_id=broadcast_channel.id, content__type=MessageType.REGULAR),
        ).only("id")
        if post is None:
            return peer, msg_id

        discussion_message = await ensure_discussion_thread(post.id)
        if discussion_message is None:
            return peer, msg_id

        return peer, discussion_message.id

    return peer, msg_id


async def get_broadcast_post(
        channel_id: int, msg_id: int, *, for_update: bool = False,
) -> MessageRef | None:
    query = MessageRef.filter(
        Q(id=msg_id, peer__channel_id=channel_id, content__type=MessageType.REGULAR),
    ).select_related("content")
    if for_update:
        query = query.select_for_update()
    return await query.first()


async def discussion_thread_id_for_post(post: MessageRef) -> int | None:
    discussion_message = await ensure_discussion_thread(post.id)
    if discussion_message is None:
        return post.discussion_id or post.discussion_top_message_id
    return discussion_message.id


async def broadcast_post_for_discussion_thread(discussion_thread_id: int) -> MessageRef | None:
    return await MessageRef.filter(
        Q(discussion_id=discussion_thread_id) | Q(discussion_top_message_id=discussion_thread_id),
        peer__channel__channel=True,
        peer__channel__is_discussion=False,
    ).select_related("content", "peer__channel").first()


async def resolve_read_discussion_target(peer: Peer, msg_id: int) -> ReadDiscussionTarget | None:
    channel = peer.channel

    if channel.channel and not channel.is_discussion:
        post = await get_broadcast_post(channel.id, msg_id)
        if post is not None:
            discussion_thread_id = await discussion_thread_id_for_post(post)
            if discussion_thread_id is not None:
                return ReadDiscussionTarget(channel, post.id, discussion_thread_id)

        broadcast_post = await broadcast_post_for_discussion_thread(msg_id)
        if broadcast_post is not None and broadcast_post.peer.channel_id == channel.id:
            return ReadDiscussionTarget(channel, broadcast_post.id, msg_id)

        return None

    if channel.is_discussion:
        discussion_message = await MessageRef.get_or_none(
            id=msg_id, peer_id=peer.id, is_discussion=True,
        )
        if discussion_message is not None:
            broadcast_post = await broadcast_post_for_discussion_thread(discussion_message.id)
            if broadcast_post is None:
                return None
            return ReadDiscussionTarget(
                broadcast_post.peer.channel,
                broadcast_post.id,
                discussion_message.id,
            )

        broadcast_channel = await Channel.get_or_none(discussion_id=channel.id, deleted=False)
        if broadcast_channel is None:
            return None

        post = await get_broadcast_post(broadcast_channel.id, msg_id)
        if post is None:
            return None
        discussion_thread_id = await discussion_thread_id_for_post(post)
        if discussion_thread_id is None:
            return None
        return ReadDiscussionTarget(broadcast_channel, post.id, discussion_thread_id)

    return None