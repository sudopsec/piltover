from collections import defaultdict
from typing import cast

from loguru import logger
from tortoise.transactions import in_transaction

import piltover.app.utils.updates_manager as upd
from piltover.app.utils.discussion_threads import ensure_discussion_thread
from piltover.app.bot_handlers import bots
from piltover.app.handlers.messages.sending import send_created_messages_internal, _resolve_noforwards
from piltover.db.enums import PeerType
from piltover.db.models import Peer, MessageRef, MessageContent, User, Presence, MessageDraft, Channel, \
    TaskIqScheduledMessage
from piltover.enums import ReqHandlerFlags
from piltover.tl import TLObject
from piltover.tl.functions.internal import SendScheduledMessage, DeleteScheduledMessage, CreateDiscussionThread, \
    ProcessMessageToBuiltinBot, UpdateStatusForPeers, ClearDraft
from piltover.tl.types.internal import TaggedBool
from piltover.worker import MessageHandler

handler = MessageHandler("internal")


@handler.on_request(SendScheduledMessage, ReqHandlerFlags.INTERNAL)
async def send_scheduled_message(request: SendScheduledMessage) -> TLObject:
    logger.trace("Processing scheduled message {message_id}", message_id=request.message_id)

    async with in_transaction():
        scheduled = await MessageRef.select_for_update(
            skip_locked=True, no_key=True,
        ).get_or_none(
            id=request.message_id,
        ).select_related(
            "taskiqscheduledmessages", "peer", "peer__owner", "peer__user", "content", "content__author",
            "content__media", "reply_to", "content__fwd_header", "content__post_info", "content__send_as_channel",
        )
        if scheduled is None:
            logger.warning(f"Scheduled message {request.message_id} does not exist?")
            return TaggedBool(value=False)

        task = cast(TaskIqScheduledMessage, scheduled.taskiqscheduledmessages)

        messages = await scheduled.send_scheduled(task.opposite)
        await scheduled.delete()

    await send_created_messages_internal(
        messages, task.opposite, scheduled.peer, scheduled.peer.owner, False, task.mentioned_users_set,
    )

    peer = scheduled.peer
    if peer.type is PeerType.CHANNEL and task.opposite:
        new_message = next(iter(messages.values()))
    else:
        new_message = messages[peer]

    await upd.delete_scheduled_messages(peer.owner_id, peer, [scheduled.id], [new_message.id])

    return TaggedBool(value=True)


@handler.on_request(DeleteScheduledMessage, ReqHandlerFlags.INTERNAL)
async def delete_scheduled_message(request: DeleteScheduledMessage) -> TLObject:
    logger.trace("Deleting scheduled-for-deletion message {message_id}", message_id=request.message_id)

    async with in_transaction():
        to_delete = await MessageRef.select_for_update(
            skip_locked=True, no_key=True,
        ).filter(content_id=request.message_id).select_related("peer", "peer__channel")

        all_ids = []
        regular_messages: dict[User | int, list[int]] = defaultdict(list)
        channel_messages: dict[Channel, list[int]] = defaultdict(list)

        for message in to_delete:
            all_ids.append(message.id)
            if message.peer.type is PeerType.CHANNEL:
                channel_messages[message.peer.channel].append(message.id)
            else:
                regular_messages[message.peer.owner_id].append(message.id)

        await MessageContent.filter(id=request.message_id).delete()

        if regular_messages:
            await upd.delete_messages(None, regular_messages)
        for channel, message_ids in channel_messages.items():
            await upd.delete_messages_channel(channel, message_ids)

    return TaggedBool(value=True)


@handler.on_request(CreateDiscussionThread, ReqHandlerFlags.INTERNAL)
async def create_discussion_thread(request: CreateDiscussionThread) -> TLObject:
    logger.trace("Creating discussion thread for channel message {message_id}", message_id=request.message_id)

    # TODO: forward media groups correctly

    logger.info(f"Creating discussion thread for message {request.message_id}")
    discussion_message = await ensure_discussion_thread(request.message_id)
    return TaggedBool(value=discussion_message is not None)


@handler.on_request(ProcessMessageToBuiltinBot, ReqHandlerFlags.INTERNAL)
async def process_message_to_builtin_bot(request: ProcessMessageToBuiltinBot) -> TLObject:
    logger.info(f"Processing message to bot {request.messageref_id}")
    message = await MessageRef.select_for_update().get_or_none(id=request.messageref_id).select_related(
        "peer", "peer__owner", "peer__user", "content", "content__media", "content__media__file",
    )
    if message is None:
        return TaggedBool(value=False)

    peer = message.peer

    bot_message = await bots.process_message_to_bot(peer, message)
    if bot_message is not None:
        await upd.send_message(None, {peer: bot_message})

    return TaggedBool(value=True)


@handler.on_request(UpdateStatusForPeers, ReqHandlerFlags.INTERNAL)
async def update_status_for_peers(request: UpdateStatusForPeers) -> TLObject:
    user = await User.get(id=request.peer_owner)
    if user.support:
        return TaggedBool(value=True)
    presence = await Presence.update_to_now(user)

    peer_type = PeerType(request.peer_type)

    peer_users: list[User]
    if peer_type is PeerType.USER:
        if request.peer_user == 777000:
            return TaggedBool(value=True)
        if await Peer.filter(
                owner_id=request.peer_user, user_id=request.peer_owner, blocked_at__not_isnull=True
        ).exists():
            return TaggedBool(value=True)
        peer_users = [await User.get(id=request.peer_user).only("id")]
    elif peer_type is PeerType.CHAT:
        peer_users = await User.filter(
            chatparticipants__chat_id=request.peer_chat, id__not=request.peer_owner
        ).only("id")
    else:
        return TaggedBool(value=False)

    await upd.update_status(user, presence, peer_users)
    return TaggedBool(value=True)


@handler.on_request(ClearDraft, ReqHandlerFlags.INTERNAL)
async def clear_draft(request: ClearDraft) -> TLObject:
    if await MessageDraft.filter(user_id=request.user_id, peer_id=request.peer_id).delete():
        peer: Peer = await Peer.get(id=request.peer_id)
        await upd.update_draft(request.user_id, peer, None)
        return TaggedBool(value=True)

    return TaggedBool(value=False)
