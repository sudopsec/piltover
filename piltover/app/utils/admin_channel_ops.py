from __future__ import annotations

import piltover.app.utils.updates_manager as upd
from tortoise.expressions import F
from piltover.db.enums import ChatAdminRights, MessageType, PeerType
from piltover.db.models import Channel, Chat, ChatParticipant, MessageRef, Peer, User
from piltover.db.models.channel import CREATOR_RIGHTS
from piltover.tl import MessageActionChatDeleteUser, Updates


async def kick_chat_member(chat: Chat, target_user_id: int, *, actor_id: int) -> None:
    chat_peer = await Peer.get(owner_id=actor_id, chat_id=chat.id, type=PeerType.CHAT)
    await MessageRef.create_for_peer(
        chat_peer, actor_id,
        type=MessageType.SERVICE_CHAT_USER_DEL,
        extra_info=MessageActionChatDeleteUser(user_id=target_user_id).write(),
    )
    await ChatParticipant.filter(chat_id=chat.id, user_id=target_user_id).delete()
    await Chat.filter(id=chat.id).update(participants_count=F("participants_count") - 1, version=F("version") + 1)


async def kick_channel_member(channel: Channel, target_user_id: int) -> None:
    participant = await ChatParticipant.get_or_none(channel_id=channel.id, user_id=target_user_id)
    if participant is None:
        return
    participant.left = True
    participant.admin_rights = ChatAdminRights(0)
    await participant.save(update_fields=["left", "admin_rights"])
    await Channel.filter(id=channel.id).update(
        participants_count=F("participants_count") - 1, version=F("version") + 1,
    )


async def promote_chat_admin(chat: Chat, target_user_id: int) -> None:
    participant = await ChatParticipant.get_or_none(chat_id=chat.id, user_id=target_user_id, left=False)
    if participant is None:
        raise ValueError("not a participant")
    participant.admin_rights = (
        ChatAdminRights.CHANGE_INFO | ChatAdminRights.DELETE_MESSAGES | ChatAdminRights.BAN_USERS
        | ChatAdminRights.INVITE_USERS | ChatAdminRights.PIN_MESSAGES
    )
    await participant.save(update_fields=["admin_rights"])


async def promote_channel_admin(channel: Channel, target_user_id: int) -> None:
    participant = await ChatParticipant.get_or_none(channel_id=channel.id, user_id=target_user_id, left=False)
    if participant is None:
        raise ValueError("not a participant")
    participant.admin_rights = ChatAdminRights.from_tl(CREATOR_RIGHTS)
    if not participant.is_admin:
        channel.admins_count += 1
        await channel.save(update_fields=["admins_count"])
    await participant.save(update_fields=["admin_rights"])


async def transfer_chat_owner(chat: Chat, new_owner_id: int) -> None:
    old_owner_id = chat.creator_id
    chat.creator_id = new_owner_id
    await chat.save(update_fields=["creator_id", "version"])

    old_p = await ChatParticipant.get_or_none(chat_id=chat.id, user_id=old_owner_id)
    new_p = await ChatParticipant.get_or_none(chat_id=chat.id, user_id=new_owner_id, left=False)
    if old_p is not None:
        old_p.admin_rights = ChatAdminRights(0)
        await old_p.save(update_fields=["admin_rights"])
    if new_p is not None:
        new_p.admin_rights = (
            ChatAdminRights.CHANGE_INFO | ChatAdminRights.DELETE_MESSAGES | ChatAdminRights.BAN_USERS
            | ChatAdminRights.INVITE_USERS | ChatAdminRights.PIN_MESSAGES | ChatAdminRights.ADD_ADMINS
        )
        await new_p.save(update_fields=["admin_rights"])
    await upd.update_chat(chat)


async def transfer_channel_owner(channel: Channel, new_owner_id: int) -> None:
    old_owner_id = channel.creator_id
    channel.creator_id = new_owner_id
    channel.version += 1
    await channel.save(update_fields=["creator_id", "version"])

    old_p = await ChatParticipant.get_or_none(channel_id=channel.id, user_id=old_owner_id)
    new_p = await ChatParticipant.get_or_none(channel_id=channel.id, user_id=new_owner_id, left=False)
    if old_p is not None:
        old_p.admin_rights = ChatAdminRights(0)
        await old_p.save(update_fields=["admin_rights"])
    if new_p is not None:
        new_p.admin_rights = ChatAdminRights.from_tl(CREATOR_RIGHTS)
        await new_p.save(update_fields=["admin_rights"])
    await channel.sync_admins_count(False)
    await upd.update_channel(channel, send_to_users=[old_owner_id, new_owner_id])


async def delete_channel_admin(channel: Channel) -> None:
    channel.deleted = True
    channel.version += 1
    await channel.save(update_fields=["deleted", "version"])
    await upd.update_channel(channel)


async def admin_delete_bot(bot_user: User) -> None:
    from piltover.app.utils.admin_delete_user import save_deleted_account_snapshot
    from piltover.app.utils.admin_sessions import kick_all_user_sessions
    from piltover.db.models import Bot, State, Username

    await save_deleted_account_snapshot(bot_user)

    bot_user.deleted = True
    bot_user.first_name = "Deleted Bot"
    bot_user.last_name = None
    await bot_user.save(update_fields=["deleted", "first_name", "last_name", "version"])
    await bot_user.inc_version()

    if await State.filter(user_id=bot_user.id).exists():
        await upd.update_user(bot_user)

    await kick_all_user_sessions(bot_user.id)
    await Bot.filter(bot_id=bot_user.id).delete()
    await Username.filter(user_id=bot_user.id).delete()
    await State.filter(user_id=bot_user.id).delete()