from piltover.db.models import Peer, MessageRef
from piltover.tl import KeyboardButtonRow, KeyboardButtonCallback, ReplyInlineMarkup, ReplyKeyboardMarkup

STAR_AMOUNTS = (1, 25, 50, 100, 1000)


def get_stars_keyboard() -> ReplyInlineMarkup:
    rows: list[KeyboardButtonRow] = []
    for idx, amount in enumerate(STAR_AMOUNTS):
        if idx % 3 == 0:
            rows.append(KeyboardButtonRow(buttons=[]))
        rows[-1].buttons.append(KeyboardButtonCallback(
            text=f"{amount} ⭐",
            data=f"get/{amount}".encode("latin1"),
        ))
    return ReplyInlineMarkup(rows=rows)


async def send_bot_message(
        peer: Peer, text: str, keyboard: ReplyInlineMarkup | ReplyKeyboardMarkup | None = None,
        entities: list[dict[str, str | int]] | None = None,
) -> MessageRef:
    messages = await MessageRef.create_for_peer(
        peer, peer.user, opposite=False,
        message=text, reply_markup=keyboard.write() if keyboard else None, entities=entities,
    )
    return messages[peer]