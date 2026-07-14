"""Backward-compatible re-exports."""

from piltover.app.bot_handlers.typetestbot.buttons import (
    BUTTON_DEMO_HANDLERS,
    buttons_menu_keyboard,
    demo_buy_invoice,
    demo_buy_mismatch,
    demo_buy_plain,
    demo_inline,
    demo_password,
    page_buttons,
)
from piltover.app.bot_handlers.typetestbot.common import (
    HUB_TEXT,
    hub_keyboard,
    send_bot_message,
)

DEMO_HANDLERS = BUTTON_DEMO_HANDLERS
menu_keyboard = buttons_menu_keyboard