from __future__ import annotations

from piltover.app.bot_handlers.typetestbot.catalog.registry import (
    ACTION_SPECIMENS,
    FLAG_SPECIMENS,
    IMPOSSIBLE_SPECIMENS,
    NOTIF_SPECIMENS,
    REGULAR_SPECIMENS,
    USER_SPECIMENS,
    _ENTITY_HANDLERS,
    _SERVICE_SPECIMENS,
    all_specimens,
)
from piltover.app.bot_handlers.typetestbot.common import (
    _pad_paired_items,
    _menu_rows,
    append_footer_rows,
    edit_bot_message,
    paired_menu,
    send_bot_message,
)
from piltover.db.models import MessageRef, Peer
from piltover.tl import ReplyInlineMarkup

_PER_PAGE = 14

_CATEGORIES: dict[str, tuple[str, list[tuple[bytes, str]]]] = {
    "regular": ("📨 Regular", [(k, l) for k, l, _ in REGULAR_SPECIMENS]),
    "flags": ("🏳 Flags", [(k, l) for k, l, _ in FLAG_SPECIMENS]),
    "service": ("⚙️ Service actions", [(s.key, s.label) for s in _SERVICE_SPECIMENS]),
    "entities": ("🔤 Entities", [(k, k.decode().rsplit(":", 1)[-1]) for k in sorted(_ENTITY_HANDLERS)]),
    "user": ("👤 As user", [(k, l) for k, l, _ in USER_SPECIMENS]),
    "notif": ("📢 Notifications", [(k, l) for k, l, _ in NOTIF_SPECIMENS]),
    "actions": ("🤖 Bot actions", list(ACTION_SPECIMENS)),
    "impossible": ("💀 Impossible", [(k, l) for k, l, _ in IMPOSSIBLE_SPECIMENS]),
}


def _paged_menu(
        items: list[tuple[bytes, str]], page: int, category: str,
) -> ReplyInlineMarkup:
    start = page * _PER_PAGE
    chunk = items[start:start + _PER_PAGE]
    menu_items = _pad_paired_items([(label, key) for key, label in chunk])
    nav_prefix = f"cat:page:{category}".encode()
    if page > 0:
        menu_items.append(("◀ Prev", nav_prefix + b":" + str(page - 1).encode()))
    if start + _PER_PAGE < len(items):
        menu_items.append(("Next ▶", nav_prefix + b":" + str(page + 1).encode()))
    markup = _menu_rows(menu_items)
    return append_footer_rows(
        markup,
        ("← Catalog", b"page:catalog"),
        ("← Hub", b"page:home"),
    )


def catalog_index_keyboard() -> ReplyInlineMarkup:
    counts: dict[str, int] = {}
    for sp in all_specimens():
        counts[sp.category] = counts.get(sp.category, 0) + 1
    markup = paired_menu([
        (f"Regular ({counts.get('regular', 0)})", b"cat:page:regular:0"),
        (f"Service ({counts.get('service', 0)})", b"cat:page:service:0"),
        (f"Entities ({counts.get('entities', 0)})", b"cat:page:entities:0"),
        (f"Bot actions ({counts.get('actions', 0)})", b"cat:page:actions:0"),
        (f"Flags ({counts.get('flags', 0)})", b"cat:page:flags:0"),
        (f"As user ({counts.get('user', 0)})", b"cat:page:user:0"),
        (f"Notif ({counts.get('notif', 0)})", b"cat:page:notif:0"),
        (f"Impossible ({counts.get('impossible', 0)})", b"cat:page:impossible:0"),
    ])
    return append_footer_rows(markup, ("← Hub", b"page:home"))


CATALOG_INDEX_TEXT = (
    "📋 Message catalog\n\n"
    "Full list of message specimens: regular media, every MessageAction,\n"
    "every MessageEntity, bot actions (typing, call…), flags,\n"
    "user-as-sender, notifications, and impossible/invalid combos.\n\n"
    "/catalog — this index"
)


async def page_catalog(peer: Peer, menu_message: MessageRef | None = None) -> MessageRef:
    if menu_message is None:
        return await send_bot_message(peer, CATALOG_INDEX_TEXT, catalog_index_keyboard())
    return await edit_bot_message(menu_message, peer, CATALOG_INDEX_TEXT, catalog_index_keyboard())


async def page_action_feedback(peer: Peer, menu_message: MessageRef, done: str) -> MessageRef:
    title, items = _CATEGORIES["actions"]
    pages = max(1, (len(items) + _PER_PAGE - 1) // _PER_PAGE)
    text = f"{title} — page 1/{pages}\n\n✓ {done}\n\nTap another action or navigate back."
    keyboard = _paged_menu(items, 0, "actions")
    return await edit_bot_message(menu_message, peer, text, keyboard)


async def page_category(peer: Peer, category: str, page: int, menu_message: MessageRef | None = None) -> MessageRef:
    title, items = _CATEGORIES[category]
    total = len(items)
    pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
    text = f"{title} — page {page + 1}/{pages}\n\n{total} specimens total. Tap to send."
    keyboard = _paged_menu(items, page, category)
    if menu_message is None:
        return await send_bot_message(peer, text, keyboard)
    return await edit_bot_message(menu_message, peer, text, keyboard)


def parse_category_page(data: bytes) -> tuple[str, int] | None:
    if not data.startswith(b"cat:page:"):
        return None
    parts = data.split(b":")
    # cat:page:regular:0
    if len(parts) != 4 or parts[2].decode() not in _CATEGORIES:
        return None
    page = int(parts[3]) if parts[3].isdigit() else 0
    return parts[2].decode(), page