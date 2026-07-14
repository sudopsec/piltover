from __future__ import annotations

import piltover.app.utils.updates_manager as upd
from piltover.app.bot_handlers.typetestbot.buttons import BUTTON_DEMO_HANDLERS, page_buttons
from piltover.app.bot_handlers.typetestbot.catalog import (
    ACTION_HANDLERS,
    CATALOG_HANDLERS,
    page_catalog,
    page_category,
    parse_category_page,
)
from piltover.app.bot_handlers.typetestbot.common import (
    HUB_TEXT,
    NAV_NOOP_CALLBACK,
    edit_bot_message,
    hub_keyboard,
    send_bot_message,
)
from piltover.db.models import MessageRef, Peer
from piltover.tl.types.messages import BotCallbackAnswer

_PAGE_HANDLERS = {
    b"page:home": lambda peer, menu: edit_bot_message(menu, peer, HUB_TEXT, hub_keyboard()),
    b"page:buttons": page_buttons,
    b"page:catalog": page_catalog,
    b"page:messages": page_catalog,
}


async def typetestbot_callback_query_handler(
        peer: Peer, message: MessageRef, data: bytes,
) -> BotCallbackAnswer | None:
    if data == NAV_NOOP_CALLBACK:
        return BotCallbackAnswer(cache_time=0)

    if parsed := parse_category_page(data):
        category, page = parsed
        await page_category(peer, category, page, message)
        return BotCallbackAnswer(cache_time=0)

    if data in _PAGE_HANDLERS:
        await _PAGE_HANDLERS[data](peer, message)
        return BotCallbackAnswer(cache_time=0)

    if data in BUTTON_DEMO_HANDLERS:
        demo_message = await BUTTON_DEMO_HANDLERS[data](peer)
        await upd.send_message(None, {peer: demo_message}, False)
        return BotCallbackAnswer(message="Demo sent.", cache_time=0)

    if data in ACTION_HANDLERS:
        await ACTION_HANDLERS[data](peer, message)
        return BotCallbackAnswer(cache_time=0)

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