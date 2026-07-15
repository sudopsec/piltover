from __future__ import annotations

from typing import cast

from tortoise.expressions import Q, F
from tortoise.transactions import in_transaction

import piltover.app.utils.updates_manager as upd
from piltover.db.enums import MessageType, PeerType, ChatAdminRights
from piltover.db.models import User, Channel, Peer, MessageRef, ForumTopic, ForumTopicReadState
from piltover.exceptions import ErrorRpc
from piltover.tl import (
    MessageActionTopicCreate, PeerNotifySettings, PeerUser,
    ForumTopic as TLForumTopic, ForumTopicDeleted, Updates,
)


TOPIC_ICON_COLORS = (
    0x6FB9F0,
    0xFFD67E,
    0xCB86DB,
    0x8EEE98,
    0xFF93B2,
    0xFB6F5F,
)

GENERAL_TOPIC_TITLE = "General"


def pick_icon_color(topic_id: int, icon_color: int | None = None) -> int:
    if icon_color is not None:
        return icon_color
    return TOPIC_ICON_COLORS[(topic_id - 1) % len(TOPIC_ICON_COLORS)]


def validate_topic_title(title: str) -> str:
    title = title.strip()
    if not title:
        raise ErrorRpc(error_code=400, error_message="TOPIC_TITLE_EMPTY")
    if len(title) > 128:
        raise ErrorRpc(error_code=400, error_message="TOPIC_TITLE_TOO_LONG")
    return title


async def get_forum_channel(user_id: int, input_channel) -> tuple[Channel, Peer]:
    peer_type, channel_id = Peer.type_and_id_from_input_raise(user_id, input_channel, "CHANNEL_PRIVATE")
    if peer_type is not PeerType.CHANNEL:
        raise ErrorRpc(error_code=400, error_message="CHANNEL_INVALID")

    channel = await Channel.get_or_none(id=channel_id, deleted=False, supergroup=True)
    if channel is None:
        raise ErrorRpc(error_code=406, error_message="CHANNEL_PRIVATE")

    peer = await Peer.get(channel_id=channel.id)
    return channel, peer


async def require_forum_channel(user_id: int, input_channel) -> tuple[Channel, Peer]:
    channel, peer = await get_forum_channel(user_id, input_channel)
    if not channel.forum:
        raise ErrorRpc(error_code=400, error_message="CHANNEL_INVALID")
    return channel, peer


async def require_manage_topics(user_id: int, channel: Channel) -> None:
    participant = await channel.get_participant_raise(user_id)
    if not channel.admin_has_permission(participant, ChatAdminRights.MANAGE_TOPICS):
        raise ErrorRpc(error_code=403, error_message="CHAT_ADMIN_REQUIRED")


async def topics_to_tl_bulk(topics: list[ForumTopic], user_id: int) -> list[TLForumTopic | ForumTopicDeleted]:
    if not topics:
        return []

    topic_ids = [topic.id for topic in topics]
    read_states = {
        state.topic_id: state.last_message_id
        for state in await ForumTopicReadState.filter(user_id=user_id, topic_id__in=topic_ids)
    }

    unread_counts: dict[int, int] = {}
    for topic in topics:
        if topic.deleted:
            continue
        last_read = read_states.get(topic.id, 0)
        unread_counts[topic.id] = await MessageRef.filter(
            peer__channel_id=topic.channel_id,
            top_message_id=topic.top_message_id,
            id__gt=last_read,
            content__author_id__not=user_id,
        ).count()

    result = []
    for topic in topics:
        if topic.deleted:
            result.append(ForumTopicDeleted(id=topic.topic_id))
            continue
        last_read = read_states.get(topic.id, 0)
        result.append(TLForumTopic(
            id=topic.topic_id,
            title=topic.title,
            icon_color=topic.icon_color,
            icon_emoji_id=topic.icon_emoji_id,
            top_message=topic.top_message_id,
            date=int(topic.created_at.timestamp()),
            from_id=PeerUser(user_id=topic.creator_id),
            read_inbox_max_id=last_read,
            read_outbox_max_id=0,
            unread_count=unread_counts.get(topic.id, 0),
            unread_mentions_count=0,
            unread_reactions_count=0,
            notify_settings=PeerNotifySettings(),
            closed=topic.closed,
            pinned=topic.pinned,
            hidden=topic.hidden,
            my=True,
        ))
    return result


async def get_topic_by_top_msg(channel: Channel, top_msg_id: int) -> ForumTopic | None:
    return await ForumTopic.get_or_none(
        channel=channel, top_message_id=top_msg_id, deleted=False,
    )


async def get_general_topic(channel: Channel) -> ForumTopic | None:
    return await ForumTopic.get_or_none(
        channel=channel, topic_id=1, deleted=False,
    ).select_related("top_message")


async def resolve_topic_top_message(
        channel: Channel, peer: Peer, top_msg_id: int, user_id: int,
) -> MessageRef:
    topic = await get_topic_by_top_msg(channel, top_msg_id)
    if topic is None:
        raise ErrorRpc(error_code=400, error_message="TOPIC_ID_INVALID")

    if topic.closed:
        participant = await channel.get_participant(user_id)
        if participant is None or not channel.admin_has_permission(participant, ChatAdminRights.MANAGE_TOPICS):
            raise ErrorRpc(error_code=400, error_message="TOPIC_CLOSED")

    anchor = await MessageRef.get_or_none(peer=peer, id=top_msg_id)
    if anchor is None:
        raise ErrorRpc(error_code=400, error_message="TOPIC_ID_INVALID")
    return anchor


async def create_forum_topic_record(
        channel: Channel, peer: Peer, user_id: int, title: str,
        icon_color: int | None = None, icon_emoji_id: int | None = None,
        topic_id: int | None = None,
) -> tuple[ForumTopic, MessageRef, Updates]:
    title = validate_topic_title(title)

    async with in_transaction():
        channel = await Channel.select_for_update().get(id=channel.id)
        if topic_id is None:
            topic_id = channel.next_topic_id
            channel.next_topic_id += 1
            await channel.save(update_fields=["next_topic_id"])

        color = pick_icon_color(topic_id, icon_color)

        messages = await MessageRef.create_for_peer(
            peer, user_id,
            type=MessageType.SERVICE_TOPIC_CREATE,
            extra_info=MessageActionTopicCreate(
                title=title, icon_color=color, icon_emoji_id=icon_emoji_id,
            ).write(),
            opposite=False,
        )
        anchor = messages[peer]

        topic = await ForumTopic.create(
            channel=channel,
            topic_id=topic_id,
            top_message=anchor,
            title=title,
            icon_color=color,
            icon_emoji_id=icon_emoji_id,
            creator_id=user_id,
        )

    updates = await upd.send_message_channel(user_id, channel, anchor)
    return topic, anchor, updates


async def ensure_general_topic(channel: Channel, peer: Peer, user_id: int) -> ForumTopic:
    existing = await get_general_topic(channel)
    if existing is not None:
        return existing

    topic, _, _ = await create_forum_topic_record(
        channel, peer, user_id, GENERAL_TOPIC_TITLE,
        icon_color=TOPIC_ICON_COLORS[0], topic_id=1,
    )
    return topic


async def enable_forum(channel: Channel, peer: Peer, user_id: int) -> None:
    if channel.forum:
        return
    channel.forum = True
    await channel.save(update_fields=["forum"])
    await Channel.filter(id=channel.id).update(version=F("version") + 1)
    await channel.refresh_from_db(["version"])
    await ensure_general_topic(channel, peer, user_id)
    await upd.update_channel(channel)


async def update_forum_topic_read_state(
        user_id: int, channel: Channel, peer: Peer, read_max_id: int,
) -> None:
    read_msg = await MessageRef.get_or_none(id=read_max_id, peer=peer).only("top_message_id")
    if read_msg is None:
        return

    top_msg_id = read_msg.top_message_id or read_max_id
    topic = await ForumTopic.get_or_none(channel=channel, top_message_id=top_msg_id, deleted=False)
    if topic is None:
        return

    state, created = await ForumTopicReadState.get_or_create(
        user_id=user_id, topic=topic, defaults={"last_message_id": read_max_id},
    )
    if not created and read_max_id > state.last_message_id:
        state.last_message_id = read_max_id
        await state.save(update_fields=["last_message_id"])


def build_topics_filter(
        channel: Channel, q: str | None, offset_topic: int, include_hidden: bool,
) -> Q:
    query = Q(channel=channel, deleted=False)
    if q:
        query &= Q(title__icontains=q)
    if offset_topic:
        query &= Q(topic_id__lt=offset_topic)
    if not include_hidden:
        query &= Q(hidden=False)
    return query