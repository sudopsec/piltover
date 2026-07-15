from __future__ import annotations

import piltover.app.utils.updates_manager as upd
from piltover.app.utils.formatable_text_with_entities import build_u8_to_u16
from piltover.db.models import MessageRef, Peer, User
from piltover.tl import KeyboardButtonCallback, KeyboardButtonRow, MessageEntityTextUrl, ReplyInlineMarkup

PAGE_SIZE = 8
HOME = b"adm:home"
HIDE = b"adm:act:hide"


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
    if getattr(user, "support", False):
        parts.append("🛟")
    if user.bot:
        parts.append("🤖")
    if user.system:
        parts.append("⚙")
    if getattr(user, "spam_blocked", False):
        parts.append("🚫")
    return "".join(parts)


def tme_username_entities(text: str, username: str) -> list[dict[str, str | int]]:
    link_text = f"@{username}"
    idx = text.find(link_text)
    if idx < 0:
        return []
    u8_to_u16 = build_u8_to_u16(text)
    end = idx + len(link_text)
    return [{
        "_": MessageEntityTextUrl.tlid(),
        "offset": u8_to_u16[idx],
        "length": u8_to_u16[end] - u8_to_u16[idx],
        "url": f"https://t.me/{username}",
    }]


def user_label(user: User, *, username: str | None = None) -> str:
    name = user.first_name
    if user.last_name:
        name = f"{name} {user.last_name}"
    badges = user_badges(user)
    suffix = f" (@{username})" if username else ""
    return _truncate(f"{badges} {name}{suffix}".strip())


def home_keyboard() -> ReplyInlineMarkup:
    return ReplyInlineMarkup(rows=[
        KeyboardButtonRow(buttons=[KeyboardButtonCallback(text="👥 Пользователи", data=b"adm:users:0")]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="🔍 Найти", data=b"adm:find:user"),
            KeyboardButtonCallback(text="🗑 Удалённые", data=b"adm:del:0"),
        ]),
        KeyboardButtonRow(buttons=[KeyboardButtonCallback(text="🛡 Админы", data=b"adm:admins:0")]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="📢 Каналы", data=b"adm:channels:0"),
            KeyboardButtonCallback(text="💬 Группы", data=b"adm:groups:0"),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="🤖 Боты", data=b"adm:bots:0"),
            KeyboardButtonCallback(text="📩 Репорты", data=b"adm:reports:0"),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="📣 Сервер", data=b"adm:server"),
            KeyboardButtonCallback(text="📊 Статистика", data=b"adm:stats"),
        ]),
    ])


def back_home_row() -> KeyboardButtonRow:
    return KeyboardButtonRow(buttons=[KeyboardButtonCallback(text="« Главное меню", data=HOME)])


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
        nav.append(KeyboardButtonCallback(text="« Назад", data=f"{page_prefix.decode()}:{page - 1}".encode()))
    if page + 1 < total_pages:
        nav.append(KeyboardButtonCallback(text="Вперёд »", data=f"{page_prefix.decode()}:{page + 1}".encode()))
    if nav:
        rows.append(KeyboardButtonRow(buttons=nav))

    rows.append(KeyboardButtonRow(buttons=[KeyboardButtonCallback(text="« Назад", data=back_data)]))
    return ReplyInlineMarkup(rows=rows)


async def send_bot_message(
        peer: Peer, text: str, keyboard: ReplyInlineMarkup | None = None,
        *, entities: list[dict[str, str | int]] | None = None,
) -> MessageRef:
    messages = await MessageRef.create_for_peer(
        peer, peer.user_id, opposite=False,
        message=text, reply_markup=keyboard.write() if keyboard else None,
        entities=entities,
    )
    return messages[peer]


async def push_bot_message(
        peer: Peer, text: str, keyboard: ReplyInlineMarkup | None = None,
        *, entities: list[dict[str, str | int]] | None = None,
) -> MessageRef:
    message = await send_bot_message(peer, text, keyboard, entities=entities)
    await upd.send_message(peer.owner_id, {peer: message}, False)
    return message


def hide_row() -> KeyboardButtonRow:
    return KeyboardButtonRow(buttons=[KeyboardButtonCallback(text="Скрыть", data=HIDE)])


async def hide_bot_message(peer: Peer, message: MessageRef) -> None:
    await upd.delete_messages(peer.owner_id, {peer.owner_id: [message.id]})
    await message.delete()