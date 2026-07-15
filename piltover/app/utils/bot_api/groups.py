from __future__ import annotations

import asyncio

from loguru import logger

from piltover.db.enums import PeerType
from piltover.db.models import BotInfo, ChatParticipant, MessageRef, Peer, User
from piltover.tl import MessageEntityBotCommand, MessageEntityMention


async def should_deliver_group_message(bot_user: User, message: MessageRef) -> bool:
    from piltover.app.utils.bot_api.updates import bot_api_updates

    if bot_api_updates.can_read_all_group_messages(bot_user.id):
        return True

    info = await BotInfo.get_or_none(user_id=bot_user.id)
    if info is not None and not info.group_privacy:
        return True

    content = message.content
    text = content.message or ""
    username = (await bot_user.get_raw_username() or "").lower()

    if text.startswith("/"):
        head = text.split()[0]
        if "@" not in head:
            return True
        if username and head.split("@", 1)[-1].lower() == username:
            return True

    for entity in (content.entities or []):
        tl_id = entity.get("_")
        if tl_id == MessageEntityBotCommand.tlid():
            return True
        if tl_id == MessageEntityMention.tlid() and username:
            mention = text[entity["offset"]:entity["offset"] + entity["length"]].lstrip("@").lower()
            if mention == username:
                return True

    if message.reply_to_id is not None:
        reply = await MessageRef.get_or_none(id=message.reply_to_id).select_related("content")
        if reply is not None and reply.content.author_id == bot_user.id:
            return True

    return False


async def notify_bot_api_recipients(
        messages: dict[Peer, MessageRef], author_id: int | None,
) -> None:
    from piltover.app.utils.bot_api import bot_api_updates

    if not messages:
        return

    content = next(iter(messages.values())).content
    if content.message is None and content.media_id is None:
        return

    channel_peer: Peer | None = None
    channel_message: MessageRef | None = None

    for msg_peer, message_ref in messages.items():
        if message_ref.content.message is None and message_ref.content.media_id is None:
            continue

        if msg_peer.type is PeerType.CHANNEL and msg_peer.owner_id is None:
            channel_peer = msg_peer
            channel_message = message_ref
            continue

        if msg_peer.owner_id is None or msg_peer.owner_id == author_id:
            continue

        owner = await User.get_or_none(id=msg_peer.owner_id)
        if owner is None or not owner.bot:
            continue

        if msg_peer.type is PeerType.USER:
            await bot_api_updates.enqueue_incoming_message(owner, msg_peer, message_ref)
        elif msg_peer.type is PeerType.CHAT:
            if await should_deliver_group_message(owner, message_ref):
                await bot_api_updates.enqueue_incoming_message(owner, msg_peer, message_ref)

    if channel_peer is None or channel_message is None:
        return

    await channel_peer.fetch_related("channel")
    as_channel_post = channel_peer.channel.channel and not channel_peer.channel.supergroup

    bot_ids = await ChatParticipant.filter(
        channel_id=channel_peer.channel_id, left=False, user__bot=True,
    ).values_list("user_id", flat=True)

    for bot_id in bot_ids:
        if bot_id == author_id:
            continue
        bot_user = await User.get(id=bot_id)
        if await should_deliver_group_message(bot_user, channel_message):
            await bot_api_updates.enqueue_incoming_message(
                bot_user, channel_peer, channel_message, channel_post=as_channel_post,
            )


async def _notify_bot_api_recipients_safe(
        messages: dict[Peer, MessageRef], author_id: int | None,
) -> None:
    try:
        await notify_bot_api_recipients(messages, author_id)
    except Exception as exc:
        logger.opt(exception=exc).warning("Failed to deliver Bot API update")


def schedule_bot_api_notification(
        messages: dict[Peer, MessageRef], author_id: int | None,
) -> None:
    asyncio.create_task(
        _notify_bot_api_recipients_safe(messages, author_id),
        name="bot-api-notify",
    )