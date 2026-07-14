from __future__ import annotations

from dataclasses import dataclass

from piltover.db.enums import ChatAdminRights
from piltover.db.models import Bot, Channel, Chat, ChatParticipant, Username


@dataclass(frozen=True)
class UserMembership:
    kind: str
    entity_id: int
    title: str
    role: str


async def get_user_memberships(user_id: int) -> list[UserMembership]:
    memberships: list[UserMembership] = []

    owned_bots = await Bot.filter(owner_id=user_id).select_related("bot")
    bot_ids = [entry.bot_id for entry in owned_bots]
    usernames = {}
    if bot_ids:
        usernames = {
            uid: name
            for uid, name in await Username.filter(user_id__in=bot_ids).values_list("user_id", "username")
        }

    for entry in owned_bots:
        bot_user = entry.bot
        username = usernames.get(bot_user.id)
        title = bot_user.first_name
        if username:
            title = f"{title} (@{username})"
        memberships.append(UserMembership(kind="bot", entity_id=bot_user.id, title=title, role="owner"))

    admin_participants = await ChatParticipant.filter(
        user_id=user_id, left=False,
    ).exclude(admin_rights=ChatAdminRights.NONE).select_related("chat", "channel")

    seen_chat_ids: set[int] = set()
    seen_channel_ids: set[int] = set()

    for participant in admin_participants:
        if participant.chat_id is not None:
            chat = participant.chat
            if chat is None or chat.deleted or chat.migrated:
                continue
            if chat.id in seen_chat_ids:
                continue
            seen_chat_ids.add(chat.id)
            role = "creator" if chat.creator_id == user_id else "admin"
            memberships.append(UserMembership(kind="group", entity_id=chat.id, title=chat.name, role=role))
        elif participant.channel_id is not None:
            channel = participant.channel
            if channel is None or channel.deleted:
                continue
            if channel.id in seen_channel_ids:
                continue
            seen_channel_ids.add(channel.id)
            kind = "channel" if channel.channel else "supergroup"
            role = "creator" if channel.creator_id == user_id else "admin"
            memberships.append(UserMembership(kind=kind, entity_id=channel.id, title=channel.name, role=role))

    for channel in await Channel.filter(creator_id=user_id, deleted=False):
        if channel.id in seen_channel_ids:
            continue
        kind = "channel" if channel.channel else "supergroup"
        memberships.append(UserMembership(kind=kind, entity_id=channel.id, title=channel.name, role="creator"))

    for chat in await Chat.filter(creator_id=user_id, deleted=False, migrated=False):
        if chat.id in seen_chat_ids:
            continue
        memberships.append(UserMembership(kind="group", entity_id=chat.id, title=chat.name, role="creator"))

    memberships.sort(key=lambda item: (item.kind, item.title.lower()))
    return memberships