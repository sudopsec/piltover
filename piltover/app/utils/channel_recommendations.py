from __future__ import annotations

import random

from piltover.db.models import Channel

RECOMMENDATIONS_LIMIT = 5


async def get_random_public_broadcast_channels(
        *, exclude_id: int | None = None, limit: int = RECOMMENDATIONS_LIMIT,
) -> list[Channel]:
    query = Channel.filter(deleted=False, channel=True, username__isnull=False)
    if exclude_id is not None:
        query = query.exclude(id=exclude_id)

    channel_ids = await query.values_list("id", flat=True)
    if not channel_ids:
        return []

    picked_ids = random.sample(list(channel_ids), min(limit, len(channel_ids)))
    channels = await Channel.filter(id__in=picked_ids)
    by_id = {channel.id: channel for channel in channels}
    return [by_id[channel_id] for channel_id in picked_ids if channel_id in by_id]