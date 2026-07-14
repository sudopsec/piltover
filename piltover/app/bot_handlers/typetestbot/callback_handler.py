from __future__ import annotations

import piltover.app.utils.updates_manager as upd
from piltover.app.bot_handlers.typetestbot.buttons import BUTTON_DEMO_HANDLERS, page_buttons
from piltover.app.bot_handlers.typetestbot.catalog import (
    CATALOG_HANDLERS,
    page_catalog,
    page_category,
    parse_category_page,
)
from piltover.app.bot_handlers.typetestbot.common import HUB_TEXT, hub_keyboard, send_bot_message
from piltover.db.models import MessageRef, Peer
from piltover.tl.types.messages import BotCallbackAnswer

_PAGE_HANDLERS = {
    b"page:home": lambda peer: send_bot_message(peer, HUB_TEXT, hub_keyboard()),
    b"page:buttons": page_buttons,
    b"page:catalog": page_catalog,
    b"page:messages": page_catalog,
}


async def typetestbot_callback_query_handler(
        peer: Peer, _message: MessageRef, data: bytes,
) -> BotCallbackAnswer | None:
    if parsed := parse_category_page(data):
        category, page = parsed
        page_message = await page_category(peer, category, page)
        await upd.send_message(None, {peer: page_message}, False)
        return BotCallbackAnswer(cache_time=0)

    if data in _PAGE_HANDLERS:
        page_message = await _PAGE_HANDLERS[data](peer)
        await upd.send_message(None, {peer: page_message}, False)
        return BotCallbackAnswer(cache_time=0)

    if data in BUTTON_DEMO_HANDLERS:
        demo_message = await BUTTON_DEMO_HANDLERS[data](peer)
        await upd.send_message(None, {peer: demo_message}, False)
        return BotCallbackAnswer(message="Demo sent.", cache_time=0)

    if data in CATALOG_HANDLERS:
        demo_message = await CATALOG_HANDLERS[data](peer)
        await upd.send_message(None, {peer: demo_message}, False)
        return BotCallbackAnswer(message="Specimen sent.", cache_time=0)

    if data == b"ping":
        return BotCallbackAnswer(message="pong", cache_time=0)

    if data == b"pwd_ok":
        return BotCallbackAnswer(
            message="Identity confirmed (2FA passed).",
            alert=True,
            cache_time=0,
        )

    if data == b"secret":
        return BotCallbackAnswer(
            message="You found the secret callback.",
            alert=True,
            cache_time=0,
        )

    return BotCallbackAnswer(message=f"Unknown callback: {data!r}", cache_time=0)