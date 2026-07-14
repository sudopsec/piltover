from types import NoneType

from piltover.app.bot_handlers.interaction_handler import BotInteractionHandler
from piltover.app.bot_handlers.typetestbot.buttons import (
    BUTTON_DEMO_HANDLERS,
    page_buttons,
)
from piltover.app.bot_handlers.typetestbot.catalog import page_catalog
from piltover.app.bot_handlers.typetestbot.catalog.registry import CATALOG_HANDLERS
from piltover.app.bot_handlers.typetestbot.common import HUB_TEXT, hub_keyboard, send_bot_message
from piltover.db.models import MessageRef, Peer


class TypeTestBotInteractionHandler(BotInteractionHandler[NoneType, NoneType]):
    def __init__(self) -> None:
        super().__init__(None)
        send = send_bot_message

        self.command("start").set_send_message_func(send).do(self._start).register()
        self.command("help").set_send_message_func(send).do(self._start).register()
        self.command("buttons").set_send_message_func(send).do(self._cmd(page_buttons)).register()
        self.command("catalog").set_send_message_func(send).do(self._cmd(page_catalog)).register()
        self.command("messages").set_send_message_func(send).do(self._cmd(page_catalog)).register()

        for name, handler in (
            ("inline", BUTTON_DEMO_HANDLERS[b"demo:inline"]),
            ("buy_plain", BUTTON_DEMO_HANDLERS[b"demo:buy_plain"]),
            ("buy_invoice", BUTTON_DEMO_HANDLERS[b"demo:buy_invoice"]),
            ("buy_mismatch", BUTTON_DEMO_HANDLERS[b"demo:buy_mismatch"]),
            ("password", BUTTON_DEMO_HANDLERS[b"demo:password"]),
            ("reply", BUTTON_DEMO_HANDLERS[b"demo:reply"]),
            ("force", BUTTON_DEMO_HANDLERS[b"demo:force"]),
            ("hide", BUTTON_DEMO_HANDLERS[b"demo:hide"]),
            ("urlauth", BUTTON_DEMO_HANDLERS[b"demo:urlauth"]),
            ("webview", BUTTON_DEMO_HANDLERS[b"demo:webview"]),
            ("game", BUTTON_DEMO_HANDLERS[b"demo:game"]),
            ("switch", BUTTON_DEMO_HANDLERS[b"demo:switch"]),
            ("weird", BUTTON_DEMO_HANDLERS[b"demo:weird"]),
        ):
            self.command(name).set_send_message_func(send).do(self._cmd(handler)).register()

        for name, key in (
            ("svc_pin", b"cat:svc:pin"),
            ("svc_custom", b"cat:svc:custom"),
            ("svc_payment", b"cat:svc:pay_sent"),
            ("notif", b"cat:notif:popup"),
            ("usr_invoice", b"cat:usr:invoice"),
            ("flg_mentioned", b"cat:flg:mentioned"),
            ("imp_svc_inv", b"cat:imp:svc_inv"),
        ):
            self.command(name).set_send_message_func(send).do(self._cmd(CATALOG_HANDLERS[key])).register()

    @staticmethod
    def _cmd(handler):
        async def wrapper(peer, _message, _state):
            return await handler(peer)
        return wrapper

    @staticmethod
    async def _start(peer, _message, _state) -> MessageRef:
        return await send_bot_message(peer, HUB_TEXT, hub_keyboard())