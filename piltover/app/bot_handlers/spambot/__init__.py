from types import NoneType

from piltover.app.bot_handlers.interaction_handler import BotInteractionHandler
from piltover.app.bot_handlers.spambot.utils import send_bot_message, spam_status_text
from piltover.db.models import MessageRef, Peer, User


class SpamBotInteractionHandler(BotInteractionHandler[NoneType, NoneType]):
    def __init__(self) -> None:
        super().__init__(None)
        self.command("start").set_send_message_func(send_bot_message).do(self._start).register()
        self.text().set_send_message_func(send_bot_message).otherwise(self._status).register()

    @staticmethod
    async def _start(peer: Peer, _message: MessageRef, _state: None) -> MessageRef:
        user = await User.get(id=peer.owner_id)
        return await send_bot_message(peer, spam_status_text(user))

    @staticmethod
    async def _status(peer: Peer, _message: MessageRef, _state: None) -> MessageRef:
        user = await User.get(id=peer.owner_id)
        return await send_bot_message(peer, spam_status_text(user))