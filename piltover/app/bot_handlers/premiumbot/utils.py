from piltover.db.models import Peer, MessageRef
from piltover.tl import KeyboardButtonRow, KeyboardButtonUrl, ReplyInlineMarkup


async def send_bot_message(
        peer: Peer, text: str, keyboard: ReplyInlineMarkup | None = None,
) -> MessageRef:
    messages = await MessageRef.create_for_peer(
        peer, peer.user, opposite=False,
        message=text, reply_markup=keyboard.write() if keyboard else None,
    )
    return messages[peer]


def get_welcome_keyboard() -> ReplyInlineMarkup:
    return ReplyInlineMarkup(rows=[
        KeyboardButtonRow(buttons=[
            KeyboardButtonUrl(text="Premium features", url="https://telegram.org/premium"),
        ]),
    ])


def get_status_keyboard() -> ReplyInlineMarkup:
    return ReplyInlineMarkup(rows=[
        KeyboardButtonRow(buttons=[
            KeyboardButtonUrl(text="Manage subscription", url="https://t.me/premiumbot?start=status"),
        ]),
    ])