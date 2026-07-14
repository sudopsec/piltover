from __future__ import annotations

from piltover.db.models import MessageRef, Peer, User
from piltover.tl import KeyboardButtonCallback, KeyboardButtonRow, ReplyInlineMarkup

PAGE_SIZE = 8
HOME = b"adm:home"


def _truncate(text: str, limit: int = 64) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def user_badges(user: User) -> str:
    parts: list[str] = []
    if user.admin:
        parts.append("🛡")
    if user.verified:
        parts.append("✓")
    if user.bot:
        parts.append("🤖")
    if user.system:
        parts.append("⚙")
    if getattr(user, "spam_blocked", False):
        parts.append("🚫")
    return "".join(parts)


def user_label(user: User, *, username: str | None = None) -> str:
    name = user.first_name
    if user.last_name:
        name = f"{name} {user.last_name}"
    badges = user_badges(user)
    suffix = f" (@{username})" if username else ""
    return _truncate(f"{badges} {name}{suffix}".strip())


def home_keyboard() -> ReplyInlineMarkup:
    return ReplyInlineMarkup(rows=[
        KeyboardButtonRow(buttons=[KeyboardButtonCallback(text="👥 Users", data=b"adm:users:0")]),
        KeyboardButtonRow(buttons=[KeyboardButtonCallback(text="🛡 Admins", data=b"adm:admins:0")]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="📢 Channels", data=b"adm:channels:0"),
            KeyboardButtonCallback(text="💬 Groups", data=b"adm:groups:0"),
        ]),
        KeyboardButtonRow(buttons=[KeyboardButtonCallback(text="📊 Statistics", data=b"adm:stats")]),
    ])


def back_home_row() -> KeyboardButtonRow:
    return KeyboardButtonRow(buttons=[KeyboardButtonCallback(text="« Main menu", data=HOME)])


def list_keyboard(
        *,
        items: list[tuple[str, bytes]],
        page: int,
        total_pages: int,
        page_prefix: bytes,
        back_data: bytes = HOME,
) -> ReplyInlineMarkup:
    rows: list[KeyboardButtonRow] = []
    for label, data in items:
        rows.append(KeyboardButtonRow(buttons=[KeyboardButtonCallback(text=label, data=data)]))

    nav: list[KeyboardButtonCallback] = []
    if page > 0:
        nav.append(KeyboardButtonCallback(text="« Prev", data=f"{page_prefix.decode()}:{page - 1}".encode()))
    if page + 1 < total_pages:
        nav.append(KeyboardButtonCallback(text="Next »", data=f"{page_prefix.decode()}:{page + 1}".encode()))
    if nav:
        rows.append(KeyboardButtonRow(buttons=nav))

    rows.append(KeyboardButtonRow(buttons=[KeyboardButtonCallback(text="« Back", data=back_data)]))
    return ReplyInlineMarkup(rows=rows)


async def send_bot_message(peer: Peer, text: str, keyboard: ReplyInlineMarkup | None = None) -> MessageRef:
    messages = await MessageRef.create_for_peer(
        peer, peer.user_id, opposite=False,
        message=text, reply_markup=keyboard.write() if keyboard else None,
    )
    return messages[peer]