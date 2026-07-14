from __future__ import annotations

from piltover.app.bot_handlers.typetestbot.common import append_footer_rows, edit_bot_message, paired_menu, send_bot_message
from piltover.app.utils.stars_manager import STARS_CURRENCY, _pack_invoice_static, make_invoice_buy_markup
from piltover.db.enums import MediaType
from piltover.db.models import MessageMedia, MessageRef, Peer
from piltover.tl import (
    KeyboardButton,
    KeyboardButtonBuy,
    KeyboardButtonCallback,
    KeyboardButtonCopy,
    KeyboardButtonGame,
    KeyboardButtonRequestPhone,
    KeyboardButtonRequestPoll,
    KeyboardButtonRow,
    KeyboardButtonSimpleWebView,
    KeyboardButtonSwitchInline,
    KeyboardButtonUrl,
    KeyboardButtonUrlAuth,
    KeyboardButtonWebView,
    MessageMediaInvoice,
    ReplyInlineMarkup,
    ReplyKeyboardForceReply,
    ReplyKeyboardHide,
    ReplyKeyboardMarkup,
)


def buttons_menu_keyboard() -> ReplyInlineMarkup:
    markup = paired_menu([
        ("Inline mix", b"demo:inline"),
        ("Buy on plain text", b"demo:buy_plain"),
        ("Buy on invoice", b"demo:buy_invoice"),
        ("Buy mismatch", b"demo:buy_mismatch"),
        ("Callback + 2FA", b"demo:password"),
        ("Reply keyboard", b"demo:reply"),
        ("Force reply", b"demo:force"),
        ("Hide keyboard", b"demo:hide"),
        ("URL auth", b"demo:urlauth"),
        ("WebView", b"demo:webview"),
        ("Game", b"demo:game"),
        ("Switch inline", b"demo:switch"),
        ("Weird combo", b"demo:weird"),
    ])
    return append_footer_rows(markup, ("← Hub", b"page:home"))


async def page_buttons(peer: Peer, menu_message: MessageRef | None = None) -> MessageRef:
    from piltover.app.bot_handlers.typetestbot.common import BUTTONS_PAGE_TEXT
    keyboard = buttons_menu_keyboard()
    if menu_message is None:
        return await send_bot_message(peer, BUTTONS_PAGE_TEXT, keyboard)
    return await edit_bot_message(menu_message, peer, BUTTONS_PAGE_TEXT, keyboard)


async def demo_inline(peer: Peer) -> MessageRef:
    return await send_bot_message(
        peer,
        "Inline button zoo:\n"
        "• URL opens a link\n"
        "• Callback pings the bot\n"
        "• Copy copies text to clipboard",
        ReplyInlineMarkup(rows=[
            KeyboardButtonRow(buttons=[
                KeyboardButtonUrl(text="Open Telegram", url="https://telegram.org"),
                KeyboardButtonCallback(text="Ping", data=b"ping"),
            ]),
            KeyboardButtonRow(buttons=[
                KeyboardButtonCopy(text="Copy secret", copy_text="typetestbot-secret-42"),
            ]),
        ]),
    )


async def demo_buy_plain(peer: Peer) -> MessageRef:
    return await send_bot_message(
        peer,
        "⚠️ KeyboardButtonBuy on plain text (no MessageMediaInvoice).",
        ReplyInlineMarkup(rows=[
            KeyboardButtonRow(buttons=[KeyboardButtonBuy(text="Pay ⭐ 999")]),
        ]),
    )


async def demo_buy_invoice(peer: Peer) -> MessageRef:
    amount = 3
    invoice_tl = MessageMediaInvoice(
        title=f"{amount} Stars",
        description="Proper invoice with matching Buy button",
        currency=STARS_CURRENCY,
        total_amount=amount,
        start_param="typetest",
    )
    media = await MessageMedia.create(
        type=MediaType.INVOICE,
        static_data=_pack_invoice_static(invoice_tl, b"typetest/invoice"),
    )
    messages = await MessageRef.create_for_peer(
        peer, peer.user_id, opposite=False,
        message="Legit Stars invoice — Buy should work here.",
        media=media,
        reply_markup=make_invoice_buy_markup(STARS_CURRENCY, amount).write(),
    )
    return messages[peer]


async def demo_buy_mismatch(peer: Peer) -> MessageRef:
    amount = 7
    invoice_tl = MessageMediaInvoice(
        title=f"{amount} Stars",
        description="Invoice total is 7, but Buy says 100",
        currency=STARS_CURRENCY,
        total_amount=amount,
        start_param="typetest-mismatch",
    )
    media = await MessageMedia.create(
        type=MediaType.INVOICE,
        static_data=_pack_invoice_static(invoice_tl, b"typetest/mismatch"),
    )
    wrong_buy = ReplyInlineMarkup(rows=[
        KeyboardButtonRow(buttons=[KeyboardButtonBuy(text="Pay ⭐ 100")]),
    ])
    messages = await MessageRef.create_for_peer(
        peer, peer.user_id, opposite=False,
        message="Invoice/media mismatch — button amount ≠ invoice total.",
        media=media,
        reply_markup=wrong_buy.write(),
    )
    return messages[peer]


async def demo_password(peer: Peer) -> MessageRef:
    return await send_bot_message(
        peer,
        "Callback with requires_password (2FA cloud password).",
        ReplyInlineMarkup(rows=[
            KeyboardButtonRow(buttons=[
                KeyboardButtonCallback(
                    text="Confirm identity",
                    data=b"pwd_ok",
                    requires_password=True,
                ),
            ]),
        ]),
    )


async def demo_reply(peer: Peer) -> MessageRef:
    return await send_bot_message(
        peer,
        "Reply keyboard — tap a button to send its label.",
        ReplyKeyboardMarkup(
            resize=True,
            single_use=True,
            rows=[
                KeyboardButtonRow(buttons=[
                    KeyboardButton(text="Hello"),
                    KeyboardButton(text="World"),
                ]),
                KeyboardButtonRow(buttons=[
                    KeyboardButtonRequestPhone(text="Share phone"),
                    KeyboardButtonRequestPoll(text="Create poll", quiz=False),
                ]),
            ],
        ),
    )


async def demo_force(peer: Peer) -> MessageRef:
    return await send_bot_message(
        peer,
        "Force reply — client should open the reply UI.",
        ReplyKeyboardForceReply(single_use=True, placeholder="Type here…"),
    )


async def demo_hide(peer: Peer) -> MessageRef:
    return await send_bot_message(
        peer,
        "ReplyKeyboardHide — dismiss custom reply keyboard.",
        ReplyKeyboardHide(),
    )


async def demo_urlauth(peer: Peer) -> MessageRef:
    return await send_bot_message(
        peer,
        "keyboardButtonUrlAuth — OAuth-style login.",
        ReplyInlineMarkup(rows=[
            KeyboardButtonRow(buttons=[
                KeyboardButtonUrlAuth(
                    text="Log in with Telegram",
                    url="https://example.com/auth",
                    button_id=1,
                ),
            ]),
        ]),
    )


async def demo_webview(peer: Peer) -> MessageRef:
    return await send_bot_message(
        peer,
        "WebView buttons.",
        ReplyInlineMarkup(rows=[
            KeyboardButtonRow(buttons=[
                KeyboardButtonWebView(text="Web App", url="https://telegram.org"),
            ]),
            KeyboardButtonRow(buttons=[
                KeyboardButtonSimpleWebView(text="Simple WebView", url="https://telegram.org"),
            ]),
        ]),
    )


async def demo_game(peer: Peer) -> MessageRef:
    return await send_bot_message(
        peer,
        "keyboardButtonGame — inline game flow.",
        ReplyInlineMarkup(rows=[
            KeyboardButtonRow(buttons=[KeyboardButtonGame(text="Play 🎮")]),
        ]),
    )


async def demo_switch(peer: Peer) -> MessageRef:
    return await send_bot_message(
        peer,
        "keyboardButtonSwitchInline.",
        ReplyInlineMarkup(rows=[
            KeyboardButtonRow(buttons=[
                KeyboardButtonSwitchInline(text="Inline here", query="test", same_peer=True),
            ]),
            KeyboardButtonRow(buttons=[
                KeyboardButtonSwitchInline(text="Inline elsewhere", query="test"),
            ]),
        ]),
    )


async def demo_weird(peer: Peer) -> MessageRef:
    return await send_bot_message(
        peer,
        "Kitchen sink: Buy + Game + 2FA callback on one message.",
        ReplyInlineMarkup(rows=[
            KeyboardButtonRow(buttons=[
                KeyboardButtonBuy(text="Buy on text again"),
                KeyboardButtonGame(text="Game"),
            ]),
            KeyboardButtonRow(buttons=[
                KeyboardButtonCallback(text="Secret", data=b"secret", requires_password=True),
                KeyboardButtonUrl(text="tg://resolve", url="tg://resolve?domain=typetestbot"),
            ]),
        ]),
    )


BUTTON_DEMO_HANDLERS = {
    b"demo:inline": demo_inline,
    b"demo:buy_plain": demo_buy_plain,
    b"demo:buy_invoice": demo_buy_invoice,
    b"demo:buy_mismatch": demo_buy_mismatch,
    b"demo:password": demo_password,
    b"demo:reply": demo_reply,
    b"demo:force": demo_force,
    b"demo:hide": demo_hide,
    b"demo:urlauth": demo_urlauth,
    b"demo:webview": demo_webview,
    b"demo:game": demo_game,
    b"demo:switch": demo_switch,
    b"demo:weird": demo_weird,
}