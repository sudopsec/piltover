from __future__ import annotations

from piltover.app.bot_handlers.adminbot.callback_data import (
    bot_open_link,
    bots_list_callback,
    encode_bot_list_key,
    encode_list_key,
    parse_bot_list_key,
    user_open_link,
)
from piltover.app.bot_handlers.adminbot.pages import _usernames_for_users, _user_nav_row
from piltover.app.bot_handlers.adminbot.utils import (
    HOME, PAGE_SIZE, back_home_row, hide_row, list_keyboard, push_bot_message, tme_username_entities,
)
from piltover.app.utils.admin_reports import (
    build_report_detail_lines, format_peer_type, format_report_reason, get_report_context,
)
from piltover.app.bot_handlers.typetestbot.common import edit_bot_message
from piltover.db.enums import AdminReportPeerType
from piltover.db.models import (
    AdminReport, Bot, BotCommand, BotInfo, Channel, Chat, ChatParticipant, MessageRef, Peer, User, Username,
)
from piltover.tl import KeyboardButtonCallback, KeyboardButtonRow, KeyboardButtonUrl, ReplyInlineMarkup


async def page_search_prompt(peer: Peer, menu: MessageRef, *, filters) -> MessageRef:
    labels = {
        "user": "пользователя (ID, @username, телефон или имя)",
        "ch": "канал/супергруппу (ID или @username)",
        "gr": "обычную группу (ID)",
        "bot": "бота (ID или @username)",
    }
    lines = [f"🔍 Отправьте {labels.get(filters.kind, 'запрос')} в чат.", ""]
    rows: list[KeyboardButtonRow] = []

    if filters.kind == "user":
        lines.append("Фильтры:")
        lines.append(f"  Системные пользователи: {'да' if filters.show_system else 'нет'}")
        lines.append(f"  Удалённые пользователи: {'да' if filters.include_deleted else 'нет'}")
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(
                text="✅ Системные" if filters.show_system else "☑️ Системные",
                data=b"adm:findf:sys",
            ),
            KeyboardButtonCallback(
                text="✅ Удалённые" if filters.include_deleted else "☑️ Удалённые",
                data=b"adm:findf:del",
            ),
        ]))
    elif filters.kind == "bot":
        lines.append("Фильтры:")
        lines.append(f"  Системные боты: {'да' if filters.show_system else 'нет'}")
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(
                text="✅ Системные" if filters.show_system else "☑️ Системные",
                data=b"adm:findf:sys",
            ),
        ]))
    elif filters.kind == "ch":
        kind_labels = {"all": "все", "channel": "только каналы", "supergroup": "только супергруппы"}
        lines.append("Фильтры:")
        lines.append(f"  Тип: {kind_labels.get(filters.channel_kind, filters.channel_kind)}")
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Сменить тип", data=b"adm:findf:channel"),
        ]))

    rows.append(back_home_row())
    return await edit_bot_message(menu, peer, "\n".join(lines), ReplyInlineMarkup(rows=rows))


async def page_bot_edit_prompt(
        peer: Peer, menu: MessageRef, bot_id: int, field: str, *, list_key: str = "b0",
) -> MessageRef:
    from piltover.app.utils.admin_bot_edit import CLEARABLE_BOT_FIELDS

    prompts = {
        "name": "Отправьте новое имя бота (макс. 64 символа).",
        "lastname": "Отправьте фамилию (макс. 64) или нажмите «Пусто», чтобы очистить.",
        "username": "Отправьте @username (1–32 символа: a-z, 0-9, _).",
        "about": "Отправьте текст «О боте» (макс. 120) или нажмите «Пусто», чтобы очистить.",
        "desc": "Отправьте описание бота (макс. 120) или нажмите «Пусто», чтобы очистить.",
        "privacy": "Отправьте URL политики конфиденциальности (https, макс. 240) или нажмите «Пусто», чтобы очистить.",
    }
    title = prompts.get(field, "Отправьте новое значение.")
    rows: list[KeyboardButtonRow] = []
    if field in CLEARABLE_BOT_FIELDS:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Пусто", data=f"adm:bot:empty:{field}:{bot_id}:{list_key}".encode()),
        ]))
    rows.append(KeyboardButtonRow(buttons=[
        KeyboardButtonCallback(text="« Отмена", data=f"adm:bot:set:{bot_id}:{list_key}".encode()),
    ]))
    return await edit_bot_message(menu, peer, f"✏️ {title}", ReplyInlineMarkup(rows=rows))


_ENTITY_EDIT_PROMPTS: dict[tuple[str, str], str] = {
    ("user", "name"): "Отправьте новое имя (макс. 128 символов).",
    ("user", "lastname"): "Отправьте фамилию (макс. 128) или нажмите «Пусто», чтобы очистить.",
    ("user", "username"): "Отправьте @username (1–32 символа: a-z, 0-9, _) или нажмите «Пусто».",
    ("user", "about"): "Отправьте «О себе» или нажмите «Пусто», чтобы очистить.",
    ("user", "phone"): "Отправьте номер телефона (только цифры) или нажмите «Пусто».",
    ("ch", "name"): "Отправьте новое название канала.",
    ("ch", "about"): "Отправьте описание (макс. 255) или нажмите «Пусто», чтобы очистить.",
    ("ch", "username"): "Отправьте @username (1–32 символа) или нажмите «Пусто».",
    ("gr", "name"): "Отправьте новое название группы.",
    ("gr", "about"): "Отправьте описание (макс. 255) или нажмите «Пусто», чтобы очистить.",
}

_ENTITY_CLEARABLE: dict[str, frozenset[str]] = {
    "user": frozenset({"lastname", "username", "about", "phone"}),
    "ch": frozenset({"about", "username"}),
    "gr": frozenset({"about"}),
}


async def page_entity_edit_prompt(
        peer: Peer, menu: MessageRef, kind: str, entity_id: int, field: str, *, list_key: str,
) -> MessageRef:
    title = _ENTITY_EDIT_PROMPTS.get((kind, field), "Отправьте новое значение.")
    rows: list[KeyboardButtonRow] = []
    if field in _ENTITY_CLEARABLE.get(kind, frozenset()):
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(
                text="Пусто", data=f"adm:{kind}:empty:{field}:{entity_id}:{list_key}".encode(),
            ),
        ]))
    rows.append(KeyboardButtonRow(buttons=[
        KeyboardButtonCallback(text="« Отмена", data=f"adm:{kind}:set:{entity_id}:{list_key}".encode()),
    ]))
    return await edit_bot_message(menu, peer, f"✏️ {title}", ReplyInlineMarkup(rows=rows))


async def page_deleted_users(peer: Peer, page: int, menu: MessageRef) -> MessageRef:
    accounts = list(await User.filter(deleted=True, system=False).order_by("-id"))
    total = len(accounts)
    if total == 0:
        return await edit_bot_message(menu, peer, "Нет удалённых аккаунтов.", list_keyboard(
            items=[], page=0, total_pages=1, page_prefix=b"adm:del",
        ))

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    chunk = accounts[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    items = [
        (
            f"{'🤖' if u.bot else '🗑'} {u.first_name} (id {u.id})",
            f"adm:delu:{u.id}:d{page}".encode(),
        )
        for u in chunk
    ]
    return await edit_bot_message(
        menu, peer, f"Удалённые аккаунты ({total}):", list_keyboard(
            items=items, page=page, total_pages=total_pages, page_prefix=b"adm:del",
        ),
    )


async def page_deleted_user(peer: Peer, user_id: int, menu: MessageRef, *, list_key: str = "d0") -> MessageRef:
    user = await User.get_or_none(id=user_id, deleted=True, system=False)
    if user is None:
        return await edit_bot_message(menu, peer, "Аккаунт не найден.", ReplyInlineMarkup(rows=[back_home_row()]))

    from piltover.db.models import BlockedPhone, DeletedAccountSnapshot
    blocked = await BlockedPhone.get_or_none(user_id=user.id)
    snapshot = await DeletedAccountSnapshot.get_or_none(user_id=user.id)

    kind = "бот" if user.bot else "пользователь"
    lines = [
        f"{'🤖' if user.bot else '🗑'} Удалённый {kind}",
        f"ID: {user.id}",
        f"Имя: {user.first_name}",
        f"Снимок: {'да' if snapshot else 'нет'}",
    ]
    if snapshot:
        if snapshot.username:
            lines.append(f"Сохранённый username: @{snapshot.username}")
        if snapshot.phone_number:
            lines.append(f"Сохранённый телефон: {snapshot.phone_number}")
        if user.bot and snapshot.bot_owner_id:
            lines.append(f"Сохранённый id владельца: {snapshot.bot_owner_id}")
    if not user.bot:
        lines.append(f"Телефон заблокирован: {'да' if blocked else 'нет'}")
        if blocked:
            lines.append(f"Заблокированный телефон: {blocked.phone_number}")

    rows = [
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(
                text="♻️ Восстановить аккаунт",
                data=f"adm:act:restore:{user.id}:{list_key}".encode(),
            ),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="« Удалённые", data=f"adm:del:{list_key[1:]}".encode()),
            KeyboardButtonCallback(text="« Главное меню", data=HOME),
        ]),
    ]
    return await edit_bot_message(menu, peer, "\n".join(lines), ReplyInlineMarkup(rows=rows))


def _bot_label(user: User, *, username: str | None = None) -> str:
    badges: list[str] = []
    if user.system:
        badges.append("⚙")
    if user.verified:
        badges.append("✓")
    badge = "".join(badges)
    prefix = f"{badge} " if badge else ""
    un = f"@{username}" if username else "—"
    name = f"{prefix}🤖 {user.first_name} ({un})"
    return name[:64]


def _bot_back_row(list_key: str) -> KeyboardButtonRow:
    page, show_system = parse_bot_list_key(list_key)
    return KeyboardButtonRow(buttons=[
        KeyboardButtonCallback(text="« Боты", data=bots_list_callback(page, show_system=show_system)),
        KeyboardButtonCallback(text="« Главное меню", data=HOME),
    ])


async def _get_bot_user(bot_id: int) -> User | None:
    return await User.get_or_none(id=bot_id, bot=True, deleted=False)


async def page_bots(peer: Peer, page: int, menu: MessageRef, *, show_system: bool = False) -> MessageRef:
    query = User.filter(bot=True, deleted=False)
    if not show_system:
        query = query.filter(system=False)
    bots = list(await query.order_by("-system", "-id"))
    total = len(bots)
    list_key = encode_bot_list_key(page, show_system=show_system)

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE) if total else 1
    page = max(0, min(page, total_pages - 1))
    list_key = encode_bot_list_key(page, show_system=show_system)
    chunk = bots[page * PAGE_SIZE:(page + 1) * PAGE_SIZE] if total else []
    usernames = await _usernames_for_users(chunk)

    scope = "все боты" if show_system else "пользовательские боты"
    text = f"🤖 Боты ({total}, {scope}). Нажмите для управления:"
    items = [
        (_bot_label(u, username=usernames.get(u.id)), f"adm:bot:{u.id}:{list_key}".encode())
        for u in chunk
    ]

    page_prefix = b"adm:bots:sys" if show_system else b"adm:bots"
    keyboard = list_keyboard(
        items=items, page=page, total_pages=total_pages, page_prefix=page_prefix,
    )
    toggle_label = "✅ Показать системных" if show_system else "☑️ Показать системных"
    keyboard.rows.insert(0, KeyboardButtonRow(buttons=[
        KeyboardButtonCallback(text="🔍 Найти бота", data=b"adm:find:bot"),
        KeyboardButtonCallback(text=toggle_label, data=bots_list_callback(page, show_system=not show_system)),
    ]))
    if total == 0:
        text = f"🤖 Нет ботов ({scope})."
    return await edit_bot_message(menu, peer, text, keyboard)


async def page_bot_unsystem_warning(
        peer: Peer, bot_id: int, menu: MessageRef, *, list_key: str = "b0",
) -> MessageRef:
    username = await Username.filter(user_id=bot_id).first().values_list("username", flat=True)
    handle = f"@{username}" if username else f"бот {bot_id}"
    lines = [
        f"⚠️ Предупреждение — {handle}",
        "",
        "Снятие системного флага с @admin нарушит работу встроенных обработчиков:",
        "• Callback-кнопки перестанут открывать админ-панель",
        "• Исходящие сообщения боту пойдут по другому пути доставки",
        "",
        "Админ-панель будет недоступна, пока системный флаг не восстановят.",
    ]
    rows = [
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(
                text="Да, снять системный флаг",
                data=f"adm:act:unsystemok:bot:{bot_id}:{list_key}".encode(),
            ),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="« Отмена", data=f"adm:bot:{bot_id}:{list_key}".encode()),
        ]),
    ]
    return await edit_bot_message(menu, peer, "\n".join(lines), ReplyInlineMarkup(rows=rows))


async def page_bot(
        peer: Peer, bot_id: int, menu: MessageRef, *, list_key: str = "b0",
        overlay: bool = False, new_message: bool = False,
) -> MessageRef:
    bot_user = await _get_bot_user(bot_id)
    if bot_user is None:
        markup = ReplyInlineMarkup(rows=[back_home_row()])
        if overlay or new_message:
            return await push_bot_message(peer, "Бот не найден.", markup)
        return await edit_bot_message(menu, peer, "Бот не найден.", markup)

    username = await bot_user.get_raw_username()
    bot_row = await Bot.get_or_none(bot_id=bot_id).select_related("owner")
    owner = bot_row.owner if bot_row else None
    commands_count = await BotCommand.filter(bot_id=bot_id).count()
    info = await BotInfo.get_or_none(user_id=bot_id)

    display_name = bot_user.first_name
    if bot_user.last_name:
        display_name = f"{display_name} {bot_user.last_name}"

    lines = [
        f"🤖 {display_name}",
        f"ID: {bot_user.id}",
        f"Юзернейм: @{username}" if username else "Юзернейм: —",
        f"Верифицирован: {'да ✓' if bot_user.verified else 'нет'}",
        f"Системный: {'да ⚙' if bot_user.system else 'нет'}",
        f"Команды: {commands_count}",
    ]
    if bot_user.about:
        lines.append(f"О боте: {bot_user.about[:100]}")
    if info and info.description:
        lines.append(f"Описание: {info.description[:100]}")
    if owner:
        lines.append(f"Владелец: {owner.first_name} (id {owner.id})")
    elif bot_user.system:
        lines.append("Владелец: — (системный бот)")

    text = "\n".join(lines)
    entities = tme_username_entities(text, username) if username else None

    rows: list[KeyboardButtonRow] = []
    if username:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonUrl(text=f"Открыть @{username}", url=f"https://t.me/{username}"),
        ]))
    rows.append(KeyboardButtonRow(buttons=[
        KeyboardButtonCallback(text="🔑 API-токен", data=f"adm:bot:token:{bot_id}:{list_key}".encode()),
        KeyboardButtonCallback(text="⚙️ Настройки", data=f"adm:bot:set:{bot_id}:{list_key}".encode()),
    ]))
    owner_row: list[KeyboardButtonCallback] = []
    if owner:
        owner_row.append(KeyboardButtonCallback(text="👤 Владелец", data=user_open_link(owner.id, list_key)))
    if bot_user.verified:
        owner_row.append(KeyboardButtonCallback(
            text="Снять ✓", data=f"adm:act:unverify:bot:{bot_id}:{list_key}".encode(),
        ))
    else:
        owner_row.append(KeyboardButtonCallback(
            text="Выдать ✓", data=f"adm:act:verify:bot:{bot_id}:{list_key}".encode(),
        ))
    if owner_row:
        rows.append(KeyboardButtonRow(buttons=owner_row))

    if bot_user.system:
        from piltover.app.utils.admin_access import is_builtin_admin_bot

        unsystem_label = "⚠️ Снять системный флаг" if await is_builtin_admin_bot(bot_user) else "Снять системный флаг"
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text=unsystem_label, data=f"adm:act:unsystem:bot:{bot_id}:{list_key}".encode()),
        ]))
    else:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Пометить как системный", data=f"adm:act:system:bot:{bot_id}:{list_key}".encode()),
            KeyboardButtonCallback(text="🗑 Удалить", data=f"adm:act:delbot:{bot_id}:{list_key}".encode()),
        ]))
    if overlay:
        rows.append(hide_row())
    else:
        rows.append(_bot_back_row(list_key))
    markup = ReplyInlineMarkup(rows=rows)
    if overlay or new_message:
        return await push_bot_message(peer, text, markup, entities=entities)
    return await edit_bot_message(menu, peer, text, markup, entities=entities)


async def page_bot_token(peer: Peer, bot_id: int, menu: MessageRef, *, list_key: str = "b0") -> MessageRef:
    bot_user = await _get_bot_user(bot_id)
    if bot_user is None:
        return await edit_bot_message(menu, peer, "Бот не найден.", ReplyInlineMarkup(rows=[back_home_row()]))

    bot_row = await Bot.get_or_none(bot_id=bot_id)
    if bot_row is None:
        return await edit_bot_message(menu, peer, "Запись бота не найдена.", ReplyInlineMarkup(rows=[_bot_back_row(list_key)]))

    token = f"{bot_id}:{bot_row.token_nonce}"
    lines = [
        f"🔑 API-токен — {bot_user.first_name}",
        "",
        token,
        "",
        "Использование с Bot API: /bot<token>/<method>",
    ]
    rows = [
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Отозвать токен", data=f"adm:act:revtoken:bot:{bot_id}:{list_key}".encode()),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="« К боту", data=f"adm:bot:{bot_id}:{list_key}".encode()),
        ]),
    ]
    return await edit_bot_message(menu, peer, "\n".join(lines), ReplyInlineMarkup(rows=rows))


async def page_bot_settings(
        peer: Peer, bot_id: int, menu: MessageRef, *, list_key: str = "b0", new_message: bool = False,
) -> MessageRef:
    bot_user = await _get_bot_user(bot_id)
    if bot_user is None:
        markup = ReplyInlineMarkup(rows=[back_home_row()])
        if new_message:
            return await push_bot_message(peer, "Бот не найден.", markup)
        return await edit_bot_message(menu, peer, "Бот не найден.", markup)

    info = await BotInfo.get_or_none(user_id=bot_id)
    commands = list(await BotCommand.filter(bot_id=bot_id).order_by("name").limit(12))
    username = await bot_user.get_raw_username()

    display_name = bot_user.first_name
    if bot_user.last_name:
        display_name = f"{display_name} {bot_user.last_name}"

    lines = [f"⚙️ Настройки — {display_name}", ""]
    lines.append(f"Имя: {bot_user.first_name}")
    lines.append(f"Фамилия: {bot_user.last_name or '—'}")
    lines.append(f"Юзернейм: @{username}" if username else "Юзернейм: —")
    lines.append(f"О боте: {bot_user.about or '—'}")
    if info is None:
        lines.append("Описание: —")
        lines.append("Политика конфиденциальности: —")
    else:
        lines.append(f"Описание: {info.description or '—'}")
        lines.append(f"Политика конфиденциальности: {info.privacy_policy_url or '—'}")
        lines.append(f"Версия BotInfo: {info.version}")

    lines.append("")
    lines.append(f"Команды ({len(commands)}):")
    if not commands:
        lines.append("—")
    else:
        for cmd in commands:
            lines.append(f"/{cmd.name} — {cmd.description[:60]}")

    rows = [
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="✏️ Имя", data=f"adm:bot:edit:name:{bot_id}:{list_key}".encode()),
            KeyboardButtonCallback(text="✏️ Фамилия", data=f"adm:bot:edit:lastname:{bot_id}:{list_key}".encode()),
        ]),
    ]
    if not bot_user.system:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="✏️ Username", data=f"adm:bot:edit:username:{bot_id}:{list_key}".encode()),
        ]))
    rows.extend([
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="✏️ О боте", data=f"adm:bot:edit:about:{bot_id}:{list_key}".encode()),
            KeyboardButtonCallback(text="✏️ Описание", data=f"adm:bot:edit:desc:{bot_id}:{list_key}".encode()),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="✏️ URL политики", data=f"adm:bot:edit:privacy:{bot_id}:{list_key}".encode()),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="« К боту", data=f"adm:bot:{bot_id}:{list_key}".encode()),
        ]),
    ])
    markup = ReplyInlineMarkup(rows=rows)
    if new_message:
        return await push_bot_message(peer, "\n".join(lines), markup)
    return await edit_bot_message(menu, peer, "\n".join(lines), markup)


async def page_user_settings(
        peer: Peer, user_id: int, menu: MessageRef, *, list_key: str = "u0", new_message: bool = False,
) -> MessageRef:
    user = await User.get_or_none(id=user_id, bot=False, deleted=False)
    if user is None:
        markup = ReplyInlineMarkup(rows=[back_home_row()])
        if new_message:
            return await push_bot_message(peer, "Пользователь не найден.", markup)
        return await edit_bot_message(menu, peer, "Пользователь не найден.", markup)

    username = await user.get_raw_username()
    display_name = user.first_name
    if user.last_name:
        display_name = f"{display_name} {user.last_name}"

    lines = [f"⚙️ Профиль — {display_name}", ""]
    lines.append(f"Имя: {user.first_name}")
    lines.append(f"Фамилия: {user.last_name or '—'}")
    lines.append(f"Юзернейм: @{username}" if username else "Юзернейм: —")
    lines.append(f"О себе: {user.about or '—'}")
    lines.append(f"Телефон: {user.phone_number or '—'}")

    rows = [
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="✏️ Имя", data=f"adm:user:edit:name:{user_id}:{list_key}".encode()),
            KeyboardButtonCallback(text="✏️ Фамилия", data=f"adm:user:edit:lastname:{user_id}:{list_key}".encode()),
        ]),
    ]
    if not user.system:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="✏️ Username", data=f"adm:user:edit:username:{user_id}:{list_key}".encode()),
        ]))
    rows.extend([
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="✏️ О себе", data=f"adm:user:edit:about:{user_id}:{list_key}".encode()),
            KeyboardButtonCallback(text="✏️ Телефон", data=f"adm:user:edit:phone:{user_id}:{list_key}".encode()),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="« К пользователю", data=f"adm:user:{user_id}:{list_key}".encode()),
        ]),
    ])
    markup = ReplyInlineMarkup(rows=rows)
    if new_message:
        return await push_bot_message(peer, "\n".join(lines), markup)
    return await edit_bot_message(menu, peer, "\n".join(lines), markup)


async def page_channel_settings(
        peer: Peer, channel_id: int, menu: MessageRef, *, list_key: str = "c0", new_message: bool = False,
) -> MessageRef:
    channel = await Channel.get_or_none(id=channel_id, deleted=False)
    if channel is None:
        markup = ReplyInlineMarkup(rows=[back_home_row()])
        if new_message:
            return await push_bot_message(peer, "Канал не найден.", markup)
        return await edit_bot_message(menu, peer, "Канал не найден.", markup)

    username_row = await Username.get_or_none(channel_id=channel.id)
    kind = "канал" if channel.channel else "супергруппа"

    lines = [f"⚙️ Профиль — [{kind}] {channel.name}", ""]
    lines.append(f"Название: {channel.name}")
    lines.append(f"Описание: {channel.description or '—'}")
    lines.append(f"Юзернейм: @{username_row.username}" if username_row else "Юзернейм: —")

    rows = [
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="✏️ Название", data=f"adm:ch:edit:name:{channel_id}:{list_key}".encode()),
            KeyboardButtonCallback(text="✏️ Описание", data=f"adm:ch:edit:about:{channel_id}:{list_key}".encode()),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="✏️ Username", data=f"adm:ch:edit:username:{channel_id}:{list_key}".encode()),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="« К каналу", data=f"adm:ch:{channel_id}:{list_key}".encode()),
        ]),
    ]
    markup = ReplyInlineMarkup(rows=rows)
    if new_message:
        return await push_bot_message(peer, "\n".join(lines), markup)
    return await edit_bot_message(menu, peer, "\n".join(lines), markup)


async def page_group_settings(
        peer: Peer, chat_id: int, menu: MessageRef, *, list_key: str = "g0", new_message: bool = False,
) -> MessageRef:
    chat = await Chat.get_or_none(id=chat_id, deleted=False, migrated=False)
    if chat is None:
        markup = ReplyInlineMarkup(rows=[back_home_row()])
        if new_message:
            return await push_bot_message(peer, "Группа не найдена.", markup)
        return await edit_bot_message(menu, peer, "Группа не найден.", markup)

    lines = [f"⚙️ Профиль — {chat.name}", ""]
    lines.append(f"Название: {chat.name}")
    lines.append(f"Описание: {chat.description or '—'}")

    rows = [
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="✏️ Название", data=f"adm:gr:edit:name:{chat_id}:{list_key}".encode()),
            KeyboardButtonCallback(text="✏️ Описание", data=f"adm:gr:edit:about:{chat_id}:{list_key}".encode()),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="« К группе", data=f"adm:gr:{chat_id}:{list_key}".encode()),
        ]),
    ]
    markup = ReplyInlineMarkup(rows=rows)
    if new_message:
        return await push_bot_message(peer, "\n".join(lines), markup)
    return await edit_bot_message(menu, peer, "\n".join(lines), markup)


async def page_channel(
        peer: Peer, channel_id: int, menu: MessageRef, *, list_key: str = "c0", new_message: bool = False,
) -> MessageRef:
    channel = await Channel.get_or_none(id=channel_id, deleted=False)
    if channel is None:
        markup = ReplyInlineMarkup(rows=[back_home_row()])
        if new_message:
            return await push_bot_message(peer, "Канал не найден.", markup)
        return await edit_bot_message(menu, peer, "Канал не найден.", markup)

    kind = "канал" if channel.channel else "супергруппа"
    username_row = await Username.get_or_none(channel_id=channel.id)
    creator = await User.get_or_none(id=channel.creator_id)

    lines = [
        f"📢 [{kind}] {channel.name}",
        f"ID: {channel.make_id()}",
        f"ID в БД: {channel.id}",
        f"Юзернейм: @{username_row.username}" if username_row else "Юзернейм: —",
        f"Участники: {channel.participants_count}",
        f"Админы: {channel.admins_count}",
        f"Верифицирован: {'да' if channel.verified else 'нет'}",
        f"Создатель: {creator.first_name if creator else '—'} (id {channel.creator_id})",
    ]
    if channel.description:
        lines.append(f"Описание: {channel.description[:120]}")

    rows = [
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="⚙️ Профиль", data=f"adm:ch:set:{channel_id}:{list_key}".encode()),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="👥 Участники", data=f"adm:ch:mem:{channel_id}:0:{list_key}".encode()),
            KeyboardButtonCallback(text="🛡 Админы", data=f"adm:ch:adm:{channel_id}:0:{list_key}".encode()),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(
                text="Снять ✓" if channel.verified else "Выдать ✓",
                data=(
                    f"adm:act:uv:ch:{channel_id}:{list_key}".encode()
                    if channel.verified else f"adm:act:v:ch:{channel_id}:{list_key}".encode()
                ),
            ),
            KeyboardButtonCallback(text="Удалить", data=f"adm:act:delch:{channel_id}:{list_key}".encode()),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Передать владельца", data=f"adm:ch:own:{channel_id}:{list_key}".encode()),
        ]),
    ]
    if new_message:
        rows.append(hide_row())
    else:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="« Каналы", data=f"adm:channels:{list_key[1:]}".encode()),
            KeyboardButtonCallback(text="« Главное меню", data=HOME),
        ]))
    markup = ReplyInlineMarkup(rows=rows)
    if new_message:
        return await push_bot_message(peer, "\n".join(lines), markup)
    return await edit_bot_message(menu, peer, "\n".join(lines), markup)


async def page_group(
        peer: Peer, chat_id: int, menu: MessageRef, *, list_key: str = "g0", new_message: bool = False,
) -> MessageRef:
    chat = await Chat.get_or_none(id=chat_id, deleted=False, migrated=False)
    if chat is None:
        markup = ReplyInlineMarkup(rows=[back_home_row()])
        if new_message:
            return await push_bot_message(peer, "Группа не найдена.", markup)
        return await edit_bot_message(menu, peer, "Группа не найдена.", markup)

    creator = await User.get_or_none(id=chat.creator_id)
    lines = [
        f"💬 {chat.name}",
        f"ID: {chat.make_id()}",
        f"Участники: {chat.participants_count}",
        f"Верифицирован: {'да' if chat.verified else 'нет'}",
        f"Создатель: {creator.first_name if creator else '—'} (id {chat.creator_id})",
    ]
    if chat.description:
        lines.append(f"Описание: {chat.description[:120]}")

    rows = [
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="⚙️ Профиль", data=f"adm:gr:set:{chat_id}:{list_key}".encode()),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="👥 Участники", data=f"adm:gr:mem:{chat_id}:0:{list_key}".encode()),
            KeyboardButtonCallback(text="🛡 Админы", data=f"adm:gr:adm:{chat_id}:0:{list_key}".encode()),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(
                text="Снять ✓" if chat.verified else "Выдать ✓",
                data=(
                    f"adm:act:uv:g:{chat_id}:{list_key}".encode()
                    if chat.verified else f"adm:act:v:g:{chat_id}:{list_key}".encode()
                ),
            ),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Передать владельца", data=f"adm:gr:own:{chat_id}:{list_key}".encode()),
        ]),
    ]
    if new_message:
        rows.append(hide_row())
    else:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="« Группы", data=f"adm:groups:{list_key[1:]}".encode()),
            KeyboardButtonCallback(text="« Главное меню", data=HOME),
        ]))
    markup = ReplyInlineMarkup(rows=rows)
    if new_message:
        return await push_bot_message(peer, "\n".join(lines), markup)
    return await edit_bot_message(menu, peer, "\n".join(lines), markup)


async def _member_lines(participants: list[ChatParticipant], users_map: dict[int, User]) -> list[str]:
    lines = []
    for p in participants:
        u = users_map.get(p.user_id)
        name = u.first_name if u else str(p.user_id)
        role = "создатель" if p.admin_rights else ("админ" if p.is_admin else "участник")
        if p.left:
            role = "вышел"
        lines.append(f"• {name} (id {p.user_id}) — {role}")
    return lines


async def _admin_participants(participants: list[ChatParticipant], creator_id: int) -> list[ChatParticipant]:
    return [p for p in participants if p.user_id == creator_id or p.is_admin]


async def page_channel_admins(
        peer: Peer, channel_id: int, page: int, menu: MessageRef, *, list_key: str = "c0",
) -> MessageRef:
    channel = await Channel.get_or_none(id=channel_id, deleted=False)
    if channel is None:
        return await edit_bot_message(menu, peer, "Не найдено.", ReplyInlineMarkup(rows=[back_home_row()]))

    all_participants = list(
        await ChatParticipant.filter(channel_id=channel_id, left=False).order_by("-admin_rights", "user_id"),
    )
    admins = await _admin_participants(all_participants, channel.creator_id)
    total = len(admins)
    chunk = admins[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    user_ids = [p.user_id for p in chunk]
    users_map = {u.id: u for u in await User.filter(id__in=user_ids)} if user_ids else {}

    lines = [f"🛡 Админы {channel.name} ({total})", ""]
    lines.extend(await _member_lines(chunk, users_map))

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    rows: list[KeyboardButtonRow] = []
    for p in chunk:
        name = users_map[p.user_id].first_name if p.user_id in users_map else str(p.user_id)
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text=f"Открыть {name[:24]}", data=user_open_link(p.user_id, list_key)),
        ]))

    nav: list[KeyboardButtonCallback] = []
    if page > 0:
        nav.append(KeyboardButtonCallback(text="«", data=f"adm:ch:adm:{channel_id}:{page - 1}:{list_key}".encode()))
    if page + 1 < total_pages:
        nav.append(KeyboardButtonCallback(text="»", data=f"adm:ch:adm:{channel_id}:{page + 1}:{list_key}".encode()))
    if nav:
        rows.append(KeyboardButtonRow(buttons=nav))
    rows.append(KeyboardButtonRow(buttons=[
        KeyboardButtonCallback(text="« Канал", data=f"adm:ch:{channel_id}:{list_key}".encode()),
    ]))
    return await edit_bot_message(menu, peer, "\n".join(lines), ReplyInlineMarkup(rows=rows))


async def page_group_admins(
        peer: Peer, chat_id: int, page: int, menu: MessageRef, *, list_key: str = "g0",
) -> MessageRef:
    chat = await Chat.get_or_none(id=chat_id, deleted=False)
    if chat is None:
        return await edit_bot_message(menu, peer, "Не найдено.", ReplyInlineMarkup(rows=[back_home_row()]))

    all_participants = list(
        await ChatParticipant.filter(chat_id=chat_id).order_by("-admin_rights", "user_id"),
    )
    admins = await _admin_participants(all_participants, chat.creator_id)
    total = len(admins)
    chunk = admins[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    user_ids = [p.user_id for p in chunk]
    users_map = {u.id: u for u in await User.filter(id__in=user_ids)} if user_ids else {}

    lines = [f"🛡 Админы {chat.name} ({total})", ""]
    lines.extend(await _member_lines(chunk, users_map))

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    rows: list[KeyboardButtonRow] = []
    for p in chunk:
        name = users_map[p.user_id].first_name if p.user_id in users_map else str(p.user_id)
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text=f"Открыть {name[:24]}", data=user_open_link(p.user_id, list_key)),
        ]))

    nav: list[KeyboardButtonCallback] = []
    if page > 0:
        nav.append(KeyboardButtonCallback(text="«", data=f"adm:gr:adm:{chat_id}:{page - 1}:{list_key}".encode()))
    if page + 1 < total_pages:
        nav.append(KeyboardButtonCallback(text="»", data=f"adm:gr:adm:{chat_id}:{page + 1}:{list_key}".encode()))
    if nav:
        rows.append(KeyboardButtonRow(buttons=nav))
    rows.append(KeyboardButtonRow(buttons=[
        KeyboardButtonCallback(text="« Группа", data=f"adm:gr:{chat_id}:{list_key}".encode()),
    ]))
    return await edit_bot_message(menu, peer, "\n".join(lines), ReplyInlineMarkup(rows=rows))


async def page_channel_members(
        peer: Peer, channel_id: int, page: int, menu: MessageRef, *, list_key: str = "c0",
) -> MessageRef:
    channel = await Channel.get_or_none(id=channel_id, deleted=False)
    if channel is None:
        return await edit_bot_message(menu, peer, "Не найдено.", ReplyInlineMarkup(rows=[back_home_row()]))

    total = await ChatParticipant.filter(channel_id=channel_id, left=False).count()
    participants = list(
        await ChatParticipant.filter(channel_id=channel_id, left=False).order_by("-admin_rights", "user_id")
        .offset(page * PAGE_SIZE).limit(PAGE_SIZE)
    )
    user_ids = [p.user_id for p in participants]
    users_map = {u.id: u for u in await User.filter(id__in=user_ids)} if user_ids else {}

    lines = [f"👥 Участники {channel.name} ({total})", ""]
    lines.extend(await _member_lines(participants, users_map))

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    rows: list[KeyboardButtonRow] = []
    for p in participants:
        if p.user_id != channel.creator_id:
            rows.append(KeyboardButtonRow(buttons=[
                KeyboardButtonCallback(
                    text=f"Исключить {users_map[p.user_id].first_name[:20] if p.user_id in users_map else p.user_id}",
                    data=f"adm:act:kickch:{channel_id}:{p.user_id}:{list_key}".encode(),
                ),
                KeyboardButtonCallback(
                    text="Сделать админом",
                    data=f"adm:act:admch:{channel_id}:{p.user_id}:{list_key}".encode(),
                ),
            ]))

    nav: list[KeyboardButtonCallback] = []
    if page > 0:
        nav.append(KeyboardButtonCallback(text="«", data=f"adm:ch:mem:{channel_id}:{page - 1}:{list_key}".encode()))
    if page + 1 < total_pages:
        nav.append(KeyboardButtonCallback(text="»", data=f"adm:ch:mem:{channel_id}:{page + 1}:{list_key}".encode()))
    if nav:
        rows.append(KeyboardButtonRow(buttons=nav))
    rows.append(KeyboardButtonRow(buttons=[
        KeyboardButtonCallback(text="« Канал", data=f"adm:ch:{channel_id}:{list_key}".encode()),
    ]))
    return await edit_bot_message(menu, peer, "\n".join(lines), ReplyInlineMarkup(rows=rows))


async def page_group_members(
        peer: Peer, chat_id: int, page: int, menu: MessageRef, *, list_key: str = "g0",
) -> MessageRef:
    chat = await Chat.get_or_none(id=chat_id, deleted=False)
    if chat is None:
        return await edit_bot_message(menu, peer, "Не найдено.", ReplyInlineMarkup(rows=[back_home_row()]))

    total = await ChatParticipant.filter(chat_id=chat_id).count()
    participants = list(
        await ChatParticipant.filter(chat_id=chat_id).order_by("-admin_rights", "user_id")
        .offset(page * PAGE_SIZE).limit(PAGE_SIZE)
    )
    user_ids = [p.user_id for p in participants]
    users_map = {u.id: u for u in await User.filter(id__in=user_ids)} if user_ids else {}

    lines = [f"👥 Участники {chat.name} ({total})", ""]
    lines.extend(await _member_lines(participants, users_map))

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    rows: list[KeyboardButtonRow] = []
    for p in participants:
        if p.user_id != chat.creator_id:
            rows.append(KeyboardButtonRow(buttons=[
                KeyboardButtonCallback(
                    text=f"Исключить {(users_map[p.user_id].first_name if p.user_id in users_map else p.user_id)}"[:24],
                    data=f"adm:act:kickgr:{chat_id}:{p.user_id}:{list_key}".encode(),
                ),
                KeyboardButtonCallback(
                    text="Сделать админом",
                    data=f"adm:act:admgr:{chat_id}:{p.user_id}:{list_key}".encode(),
                ),
            ]))

    nav: list[KeyboardButtonCallback] = []
    if page > 0:
        nav.append(KeyboardButtonCallback(text="«", data=f"adm:gr:mem:{chat_id}:{page - 1}:{list_key}".encode()))
    if page + 1 < total_pages:
        nav.append(KeyboardButtonCallback(text="»", data=f"adm:gr:mem:{chat_id}:{page + 1}:{list_key}".encode()))
    if nav:
        rows.append(KeyboardButtonRow(buttons=nav))
    rows.append(KeyboardButtonRow(buttons=[
        KeyboardButtonCallback(text="« Группа", data=f"adm:gr:{chat_id}:{list_key}".encode()),
    ]))
    return await edit_bot_message(menu, peer, "\n".join(lines), ReplyInlineMarkup(rows=rows))


async def _report_list_label(report: AdminReport) -> str:
    mark = "✓" if report.reviewed else "•"
    kind = format_peer_type(report.peer_type).split()[0]
    reason = format_report_reason(report.reason)
    return f"{mark} #{report.id} [{kind}] {reason}"[:64]


async def page_reports(peer: Peer, page: int, menu: MessageRef) -> MessageRef:
    reports = list(await AdminReport.filter().order_by("-id"))
    total = len(reports)
    pending = sum(1 for r in reports if not r.reviewed)

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE) if total else 1
    page = max(0, min(page, total_pages - 1))
    chunk = reports[page * PAGE_SIZE:(page + 1) * PAGE_SIZE] if total else []

    items = []
    for r in chunk:
        items.append((await _report_list_label(r), f"adm:report:{r.id}:r{page}".encode()))

    text = f"📩 Репорты ({total}, {pending} ожидают). Нажмите для подробностей:"
    if total == 0:
        text = "📩 Репортов пока нет."
    return await edit_bot_message(
        menu, peer, text, list_keyboard(
            items=items, page=page, total_pages=total_pages, page_prefix=b"adm:reports",
        ),
    )


async def page_report(
        peer: Peer, report_id: int, menu: MessageRef, *, list_key: str = "r0", overlay: bool = False,
) -> MessageRef:
    report = await AdminReport.get_or_none(id=report_id)
    if report is None:
        markup = ReplyInlineMarkup(rows=[back_home_row()])
        if overlay:
            return await push_bot_message(peer, "Репорт не найден.", markup)
        return await edit_bot_message(menu, peer, "Репорт не найден.", markup)

    lines = await build_report_detail_lines(report)
    ctx = await get_report_context(report)
    rows: list[KeyboardButtonRow] = []

    if report.peer_type is AdminReportPeerType.USER:
        if ctx.target_is_bot:
            rows.append(KeyboardButtonRow(buttons=[
                KeyboardButtonCallback(text="🤖 Открыть бота", data=bot_open_link(report.peer_id, list_key)),
                KeyboardButtonCallback(
                    text="🗑 Забанить бота",
                    data=f"adm:act:banbotrep:{report_id}:{report.peer_id}:{list_key}".encode(),
                ),
            ]))
        else:
            rows.append(KeyboardButtonRow(buttons=[
                KeyboardButtonCallback(text="👤 Открыть пользователя", data=user_open_link(report.peer_id, list_key)),
                KeyboardButtonCallback(
                    text="🚫 Спам-блок",
                    data=f"adm:act:spamrep:{report_id}:{report.peer_id}:{list_key}".encode(),
                ),
                KeyboardButtonCallback(
                    text="🗑 Забанить пользователя",
                    data=f"adm:act:banrep:{report_id}:{report.peer_id}:{list_key}".encode(),
                ),
            ]))
        if ctx.author_id is not None and ctx.author_id != report.peer_id:
            author_row = []
            if ctx.author_is_bot:
                author_row.append(KeyboardButtonCallback(
                    text="🤖 Открыть бота автора", data=bot_open_link(ctx.author_id, list_key),
                ))
                author_row.append(KeyboardButtonCallback(
                    text="🗑 Забанить бота автора",
                    data=f"adm:act:banbotrep:{report_id}:{ctx.author_id}:{list_key}".encode(),
                ))
            else:
                author_row.append(KeyboardButtonCallback(
                    text="👤 Открыть автора", data=user_open_link(ctx.author_id, list_key),
                ))
                author_row.append(KeyboardButtonCallback(
                    text="🚫 Спам-блок автора",
                    data=f"adm:act:spamauthrep:{report_id}:{ctx.author_id}:{list_key}".encode(),
                ))
                author_row.append(KeyboardButtonCallback(
                    text="🗑 Забанить автора",
                    data=f"adm:act:banrep:{report_id}:{ctx.author_id}:{list_key}".encode(),
                ))
            rows.append(KeyboardButtonRow(buttons=author_row))
    elif report.peer_type is AdminReportPeerType.CHANNEL:
        channel_row = [
            KeyboardButtonCallback(
                text="📢 Открыть канал",
                data=f"adm:ch:open:{report.peer_id}:{list_key}".encode(),
            ),
        ]
        if ctx.author_id is not None:
            if ctx.author_is_bot:
                channel_row.append(KeyboardButtonCallback(
                    text="🤖 Открыть бота автора", data=bot_open_link(ctx.author_id, list_key),
                ))
            else:
                channel_row.append(KeyboardButtonCallback(
                    text="👤 Открыть автора", data=user_open_link(ctx.author_id, list_key),
                ))
        rows.append(KeyboardButtonRow(buttons=channel_row))
        if ctx.author_id is not None:
            if ctx.author_is_bot:
                rows.append(KeyboardButtonRow(buttons=[
                    KeyboardButtonCallback(
                        text="🗑 Забанить бота",
                        data=f"adm:act:banbotrep:{report_id}:{ctx.author_id}:{list_key}".encode(),
                    ),
                ]))
            else:
                rows.append(KeyboardButtonRow(buttons=[
                    KeyboardButtonCallback(
                        text="🗑 Забанить автора",
                        data=f"adm:act:banrep:{report_id}:{ctx.author_id}:{list_key}".encode(),
                    ),
                    KeyboardButtonCallback(
                        text="🚫 Спам-блок автора",
                        data=f"adm:act:spamauthrep:{report_id}:{ctx.author_id}:{list_key}".encode(),
                    ),
                ]))
    elif report.peer_type is AdminReportPeerType.CHAT:
        group_row = [
            KeyboardButtonCallback(
                text="💬 Открыть группу",
                data=f"adm:gr:open:{report.peer_id}:{list_key}".encode(),
            ),
        ]
        if ctx.author_id is not None:
            if ctx.author_is_bot:
                group_row.append(KeyboardButtonCallback(
                    text="🤖 Открыть бота автора", data=bot_open_link(ctx.author_id, list_key),
                ))
            else:
                group_row.append(KeyboardButtonCallback(
                    text="👤 Открыть автора", data=user_open_link(ctx.author_id, list_key),
                ))
        rows.append(KeyboardButtonRow(buttons=group_row))
        if ctx.author_id is not None:
            if ctx.author_is_bot:
                rows.append(KeyboardButtonRow(buttons=[
                    KeyboardButtonCallback(
                        text="🗑 Забанить бота",
                        data=f"adm:act:banbotrep:{report_id}:{ctx.author_id}:{list_key}".encode(),
                    ),
                ]))
            else:
                rows.append(KeyboardButtonRow(buttons=[
                    KeyboardButtonCallback(
                        text="🗑 Забанить автора",
                        data=f"adm:act:banrep:{report_id}:{ctx.author_id}:{list_key}".encode(),
                    ),
                    KeyboardButtonCallback(
                        text="🚫 Спам-блок автора",
                        data=f"adm:act:spamauthrep:{report_id}:{ctx.author_id}:{list_key}".encode(),
                    ),
                ]))

    rows.append(KeyboardButtonRow(buttons=[
        KeyboardButtonCallback(text="👤 Открыть отправителя", data=user_open_link(report.reporter_id, list_key)),
    ]))
    if not report.reviewed:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Отметить рассмотренным", data=f"adm:act:revrep:{report_id}:{list_key}".encode()),
        ]))
    if overlay:
        rows.append(hide_row())
    else:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="« Репорты", data=f"adm:reports:{list_key[1:]}".encode()),
            KeyboardButtonCallback(text="« Главное меню", data=HOME),
        ]))
    markup = ReplyInlineMarkup(rows=rows)
    if overlay:
        return await push_bot_message(peer, "\n".join(lines), markup)
    return await edit_bot_message(menu, peer, "\n".join(lines), markup)