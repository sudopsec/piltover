from types import NoneType

from piltover.app.bot_handlers.interaction_handler import BotInteractionHandler
from piltover.app.bot_handlers.stars.utils import send_bot_message, get_stars_keyboard
from piltover.db.models import Peer, MessageRef

_START_TEXT = "⭐ Get stars for free\nChoose how many stars you want below:"


class StarsBotInteractionHandler(BotInteractionHandler[NoneType, NoneType]):
    def __init__(self) -> None:
        super().__init__(None)
        self.command("start").set_send_message_func(send_bot_message).do(self._start).register()

    @staticmethod
    async def _start(peer: Peer, _message: MessageRef, _state: None) -> MessageRef:
        return await send_bot_message(peer, _START_TEXT, get_stars_keyboard())