from __future__ import annotations

import hashlib

from piltover.app.utils.channel_recommendations import get_random_public_broadcast_channels
from piltover.db.models import Channel, Username
from piltover.tl import SponsoredMessage
from piltover.tl.types.messages import SponsoredMessages, SponsoredMessagesEmpty

_TEST_AD_COPY = (
    {
        "title": "Piltover Test Ad",
        "message": "Тестовая реклама внутри канала — как в Telegram Desktop.",
        "button_text": "Подписаться",
        "sponsor_info": "Реклама",
        "additional_info": "Тестовый спонсор Piltover",
    },
    {
        "title": "Ещё один тест",
        "message": "Второе тестовое объявление для проверки ленты канала.",
        "button_text": "Открыть",
        "sponsor_info": "Реклама",
        "additional_info": None,
    },
)


def _stable_sponsored_random_id(viewing_channel_id: int, promoted_channel_id: int, ad_index: int) -> bytes:
    return hashlib.sha256(
        f"piltover:sponsored:{viewing_channel_id}:{promoted_channel_id}:{ad_index}".encode(),
    ).digest()[:16]


async def build_channel_sponsored_messages(
        viewing_channel: Channel, *, posts_between: int = 5,
) -> SponsoredMessages | SponsoredMessagesEmpty:
    promoted_channels = await get_random_public_broadcast_channels(
        exclude_id=viewing_channel.id, limit=len(_TEST_AD_COPY),
    )
    if not promoted_channels:
        return SponsoredMessagesEmpty()

    usernames = {
        row["channel_id"]: row["username"]
        for row in await Username.filter(channel_id__in=[ch.id for ch in promoted_channels]).values("channel_id", "username")
    }

    messages: list[SponsoredMessage] = []
    chats: list = []
    for ad_index, (ad_copy, promoted) in enumerate(zip(_TEST_AD_COPY, promoted_channels)):
        username = usernames.get(promoted.id)
        url = f"https://t.me/{username}" if username else "https://t.me"
        messages.append(SponsoredMessage(
            recommended=True,
            can_report=True,
            random_id=_stable_sponsored_random_id(viewing_channel.id, promoted.id, ad_index),
            url=url,
            title=ad_copy["title"],
            message=ad_copy["message"],
            button_text=ad_copy["button_text"],
            sponsor_info=ad_copy["sponsor_info"],
            additional_info=ad_copy["additional_info"],
        ))
        chats.append(promoted)

    return SponsoredMessages(
        posts_between=posts_between,
        messages=messages,
        chats=await Channel.to_tl_bulk(chats),
        users=[],
    )