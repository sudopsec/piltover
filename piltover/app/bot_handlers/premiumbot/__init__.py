from types import NoneType

from piltover.app.bot_handlers.interaction_handler import BotInteractionHandler
from piltover.app.bot_handlers.premiumbot.utils import (
    get_status_keyboard, get_welcome_keyboard, send_bot_message,
)
from piltover.db.models import Peer, MessageRef, User

_WELCOME_TEXT = (
    "⭐ Telegram Premium\n\n"
    "Get exclusive features: faster downloads, bigger uploads, unique reactions, "
    "voice-to-text, and more.\n\n"
    "Send /start status to check your subscription."
)

_STATUS_INACTIVE_TEXT = (
    "You do not have an active Telegram Premium subscription.\n\n"
    "Premium purchases are not available on this server yet."
)


class PremiumBotInteractionHandler(BotInteractionHandler[NoneType, NoneType]):
    def __init__(self) -> None:
        super().__init__(None)
        self.command("start").set_send_message_func(send_bot_message).do(self._start).register()

    @staticmethod
    async def _start(peer: Peer, message: MessageRef, _state: None) -> MessageRef:
        text = message.content.message or ""
        args = text.split(maxsplit=1)
        if len(args) > 1 and args[1].lower() == "status":
            return await PremiumBotInteractionHandler._status(peer)
        return await send_bot_message(peer, _WELCOME_TEXT, get_welcome_keyboard())

    @staticmethod
    async def _status(peer: Peer) -> MessageRef:
        user = await User.get(id=peer.owner_id)
        if user.bot:
            text = "Bots cannot have a Premium subscription."
            return await send_bot_message(peer, text)

        return await send_bot_message(peer, _STATUS_INACTIVE_TEXT, get_status_keyboard())