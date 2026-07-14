from typing import cast, Callable, Awaitable

from piltover.app.bot_handlers.botfather import BotfatherBotInteractionHandler
from piltover.app.bot_handlers.botfather.callback_handler import botfather_callback_query_handler
from piltover.app.bot_handlers.gif.inline_handler import gif_inline_query_handler
from piltover.app.bot_handlers.interaction_handler import BotInteractionHandler
from piltover.app.bot_handlers.stars import StarsBotInteractionHandler
from piltover.app.bot_handlers.stars.callback_handler import stars_callback_query_handler
from piltover.app.bot_handlers.premiumbot import PremiumBotInteractionHandler
from piltover.app.bot_handlers.typetestbot import TypeTestBotInteractionHandler
from piltover.app.bot_handlers.typetestbot.callback_handler import typetestbot_callback_query_handler
from piltover.app.bot_handlers.verifybot import VerifyBotInteractionHandler
from piltover.app.bot_handlers.verifybot.callback_handler import verifybot_callback_query_handler
from piltover.app.bot_handlers.adminbot import AdminBotInteractionHandler
from piltover.app.bot_handlers.adminbot.callback_handler import adminbot_callback_query_handler
from piltover.app.bot_handlers.spambot import SpamBotInteractionHandler
from piltover.app.utils.admin_access import ensure_admin_bot_access
from piltover.app.bot_handlers.stars_pay import StarsPayBotInteractionHandler
from piltover.app.bot_handlers.stars_pay.callback_handler import stars_pay_callback_query_handler
from piltover.app.bot_handlers.stickers import StickersBotInteractionHandler
from piltover.app.bot_handlers.system import SystemBotInteractionHandler
from piltover.app.bot_handlers.test_bot import PingTestBotBotInteractionHandler
from piltover.db.models import Peer, InlineQuery, InlineQueryResult, InlineQueryResultItem, MessageRef, \
    BotPrecheckoutQuery, User
from piltover.exceptions import ErrorRpc
from piltover.tl import Updates
from piltover.tl.types.messages import BotCallbackAnswer, BotResults

PrecheckoutHandlerResult = bool | str | None
PrecheckoutQueryHandler = Callable[[BotPrecheckoutQuery], Awaitable[PrecheckoutHandlerResult]]
PaymentSuccessHandler = Callable[[User, Peer, int, str], Awaitable[Updates | None]]


async def _awaitable_none(_p: Peer, _m: MessageRef) -> None:
    return None


HANDLERS: dict[str, BotInteractionHandler] = {
    "test_bot": PingTestBotBotInteractionHandler(),
    "system": SystemBotInteractionHandler(),
    "botfather": BotfatherBotInteractionHandler(),
    "stickers": StickersBotInteractionHandler(),
    "stars": StarsBotInteractionHandler(),
    "stars_pay": StarsPayBotInteractionHandler(),
    "premiumbot": PremiumBotInteractionHandler(),
    "typetestbot": TypeTestBotInteractionHandler(),
    "verifybot": VerifyBotInteractionHandler(),
    "admin": AdminBotInteractionHandler(),
    "spambot": SpamBotInteractionHandler(),
}
CALLBACK_QUERY_HANDLERS: dict[str, Callable[[Peer, MessageRef, bytes], Awaitable[BotCallbackAnswer | None]]] = {
    "botfather": botfather_callback_query_handler,
    "stars": stars_callback_query_handler,
    "stars_pay": stars_pay_callback_query_handler,
    "typetestbot": typetestbot_callback_query_handler,
    "verifybot": verifybot_callback_query_handler,
    "admin": adminbot_callback_query_handler,
}
INLINE_QUERY_HANDLERS: dict[str, Callable[[InlineQuery], Awaitable[tuple[BotResults, bool] | None]]] = {
    "gif": gif_inline_query_handler,
}
PRECHECKOUT_QUERY_HANDLERS: dict[str, PrecheckoutQueryHandler] = {}
PAYMENT_SUCCESS_HANDLERS: dict[str, PaymentSuccessHandler] = {}


async def _get_bot_username(bot_id: int) -> str | None:
    user = await User.get(id=bot_id)
    return await user.get_raw_username()


async def try_notify_payment_success(
        bot_user_id: int, payer: User, payer_peer: Peer, stars: int, title: str,
) -> Updates | None:
    bot_username = await _get_bot_username(bot_user_id)
    if bot_username is None or bot_username not in PAYMENT_SUCCESS_HANDLERS:
        return None
    return await PAYMENT_SUCCESS_HANDLERS[bot_username](payer, payer_peer, stars, title)


async def try_process_precheckout_query(query: BotPrecheckoutQuery) -> bool:
    bot_username = await _get_bot_username(query.bot_id)
    if bot_username is None or bot_username not in PRECHECKOUT_QUERY_HANDLERS:
        return False

    result = await PRECHECKOUT_QUERY_HANDLERS[bot_username](query)
    if result is None:
        return False
    if result is True:
        return True
    if isinstance(result, str):
        raise ErrorRpc(error_code=400, error_message=result or "PAYMENT_FAILED")
    return False


async def process_message_to_bot(peer: Peer, message: MessageRef) -> MessageRef | None:
    if not peer.user.bot or await peer.user.get_raw_username() not in HANDLERS:
        return None
    if message.content.message is None:
        return None

    bot_username = await peer.user.get_raw_username()
    try:
        await ensure_admin_bot_access(peer.owner_id, bot_username)
    except ErrorRpc:
        return None

    handler = HANDLERS[bot_username]

    text = cast(str, message.content.message)
    if not text.startswith("/"):
        return await handler.handle_text(peer, message)

    command_name = text.split(" ", 1)[0][1:]
    return await handler.handle_command(command_name, peer, message)


async def process_callback_query(peer: Peer, message: MessageRef, data: bytes) -> BotCallbackAnswer | None:
    if not peer.user.bot or await peer.user.get_raw_username() not in CALLBACK_QUERY_HANDLERS:
        return None
    if message.content.message is None:
        return None

    bot_username = await peer.user.get_raw_username()
    try:
        await ensure_admin_bot_access(peer.owner_id, bot_username)
    except ErrorRpc:
        return None

    return await CALLBACK_QUERY_HANDLERS[bot_username](peer, message, data)


async def process_inline_query(
        inline_query: InlineQuery,
) -> tuple[InlineQueryResult, list[InlineQueryResultItem]] | None:
    if not inline_query.bot.bot or await inline_query.bot.get_raw_username() not in INLINE_QUERY_HANDLERS:
        return None

    bot_username = await inline_query.bot.get_raw_username()

    return await INLINE_QUERY_HANDLERS[bot_username](inline_query)


from piltover.app.bot_handlers.stars_pay.payment_success import stars_pay_payment_success

PAYMENT_SUCCESS_HANDLERS["stars_pay"] = stars_pay_payment_success
