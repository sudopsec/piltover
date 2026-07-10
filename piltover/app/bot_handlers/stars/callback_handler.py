import piltover.app.utils.updates_manager as upd
from piltover.app.utils.stars_manager import grant_stars
from piltover.db.models import Peer, MessageRef
from piltover.tl.types.messages import BotCallbackAnswer

_ALLOWED_AMOUNTS = frozenset({1, 25, 50, 100, 1000})


async def stars_callback_query_handler(peer: Peer, _message: MessageRef, data: bytes) -> BotCallbackAnswer | None:
    if not data.startswith(b"get/"):
        return None

    try:
        amount = int(data[4:])
    except ValueError:
        return None

    if amount not in _ALLOWED_AMOUNTS:
        return None

    balance = await grant_stars(
        peer.owner_id,
        amount,
        title="Stars Bonus",
        description=f"Received {amount} Telegram Stars from @stars",
    )
    await upd.update_stars_balance(peer.owner_id, balance.to_stars_amount())

    return BotCallbackAnswer(
        message=f"You received {amount} stars!",
        cache_time=0,
    )