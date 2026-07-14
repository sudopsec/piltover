from __future__ import annotations

import piltover.app.utils.updates_manager as upd
from piltover.app.bot_handlers.adminbot.callback_data import decode_stars_wait_data
from piltover.app.bot_handlers.adminbot.utils import send_bot_message
from piltover.app.bot_handlers.interaction_handler import BotInteractionHandler
from piltover.app.utils.stars_manager import set_stars_balance
from piltover.db.enums import AdminBotState
from piltover.db.models import AdminBotUserState, MessageRef, Peer, User
from piltover.exceptions import ErrorRpc


class AdminBotTextHandler(BotInteractionHandler[AdminBotState, AdminBotUserState]):
    def __init__(self) -> None:
        super().__init__(AdminBotUserState)
        (
            self.text()
            .set_send_message_func(send_bot_message)
            .when(state=AdminBotState.WAIT_STARS_AMOUNT)
            .do(self._custom_stars_amount)
            .delete_state()
            .register()
        )

    @staticmethod
    async def _custom_stars_amount(peer: Peer, message: MessageRef, state: AdminBotUserState) -> MessageRef:
        text = message.content.message
        if text is None:
            return await send_bot_message(peer, "Please send a number.")

        try:
            amount = int(text.strip())
        except ValueError:
            return await send_bot_message(peer, "Invalid number. Send an integer star balance.")

        if amount < 0:
            return await send_bot_message(peer, "Amount must be zero or positive.")

        target_user_id, list_key = decode_stars_wait_data(state.data)
        target = await User.get_or_none(id=target_user_id, bot=False, system=False, deleted=False)
        if target is None:
            return await send_bot_message(peer, "Target user not found.")

        try:
            balance = await set_stars_balance(
                target_user_id,
                amount,
                title="Admin set",
                description=f"Custom balance {amount} stars via @admin",
            )
        except ErrorRpc as exc:
            return await send_bot_message(peer, exc.error_message)

        await upd.update_stars_balance(target_user_id, balance.to_stars_amount())
        return await send_bot_message(peer, f"Balance set to {amount} stars for {target.first_name}.")