from __future__ import annotations

from piltover.db.models import MessageRef, Peer, User
from piltover.tl import ReplyInlineMarkup


def spam_status_text(user: User) -> str:
    if user.spam_blocked:
        return (
            "Spam Info Bot\n\n"
            "Your account is currently limited because our systems detected suspicious activity.\n\n"
            "While limited, you cannot send messages to other users. "
            "Contact a server administrator if you believe this is a mistake."
        )
    return (
        "Spam Info Bot\n\n"
        "Good news, no limits are currently applied to your account.\n\n"
        "You're free to use Telegram as usual."
    )


async def send_bot_message(peer: Peer, text: str, keyboard: ReplyInlineMarkup | None = None) -> MessageRef:
    messages = await MessageRef.create_for_peer(
        peer, peer.user_id, opposite=False,
        message=text, reply_markup=keyboard.write() if keyboard else None,
    )
    return messages[peer]