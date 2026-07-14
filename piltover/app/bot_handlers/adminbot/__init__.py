from piltover.app.bot_handlers.adminbot.text_handler import AdminBotTextHandler
from piltover.app.bot_handlers.interaction_handler import BotInteractionHandler
from piltover.app.bot_handlers.adminbot.utils import home_keyboard, send_bot_message
from piltover.db.enums import AdminBotState
from piltover.db.models import AdminBotUserState, MessageRef, Peer

_START_TEXT = (
    "🛡 Admin Panel\n\n"
    "Server administration. Choose a category below."
)


class AdminBotInteractionHandler(BotInteractionHandler[AdminBotState, AdminBotUserState]):
    def __init__(self) -> None:
        super().__init__(AdminBotUserState)
        self.include(AdminBotTextHandler())
        self.command("start").set_send_message_func(send_bot_message).do(self._start).register()

    @staticmethod
    async def _start(peer: Peer, _message: MessageRef, _state: AdminBotUserState | None) -> MessageRef:
        return await send_bot_message(peer, _START_TEXT, home_keyboard())